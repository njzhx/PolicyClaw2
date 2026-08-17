# -*- coding: utf-8 -*-
"""泰州市系列爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 City 目录下的泰州各单站爬虫复用。

泰州各站基于同一套 CMS（jpaas 集约化平台），列表页分三种形态：

1. jpaas 动态列表：页面内嵌 AuthorizedRead ``queryData``，经
   ``/api-gateway/jpaas-publish-server/front/page/build/unit``
   GET JSON 接口分页，返回 ``data.html`` 片段（``li.clearfix`` +
   ``div.pagination`` 的 ``rows``/``count`` 属性控制翻页）；
2. 信息公开聚合页（各部门“政策文件”栏目）：``.xxgk-zcnr`` 内静态
   ``li.clearfix``（部门文件/部门政策解读各 5 条），经 ``a.xxgk_more``
   链接到子栏目 jpaas 列表页，本模块会递归抓子栏目完整分页；
3. 静态年度归档列表（市政府政策文件解读）：静态 ``li.clearfix`` +
   ``/20XXn/index.html`` 年度归档子页（jpaas），按日期窗口回溯。

另有两个外部平台单独提供抓取函数：

- 省自然资源厅 gtapp 平台（泰州市自然资源和规划局_政策法规）：
  ``td.nlist`` 列表 + POST ``cpage`` 分页；
- 省税务局 chinatax 平台（国家税务总局泰州市税务局_通知公告）：
  ``/module/web/jpage/dataproxy.jsp`` XML 分页。
"""

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from crawler_http import CrawlerSession


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 30

DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
ART_HREF_RE = re.compile(r"/art/", re.IGNORECASE)
QUERY_DATA_RE = re.compile(r'queryData="(\{.*?\})"', re.S)
JPAAS_API_PATH = "/api-gateway/jpaas-publish-server/front/page/build/unit"
YEAR_ARCHIVE_RE = re.compile(r"/20\d{2}n/index\.html$")
NEXTGROUP_RE = re.compile(r"<nextgroup><!\[CDATA\[<a href=\"([^\"]+)\"", re.S)
RECORD_RE = re.compile(r"<record><!\[CDATA\[(.*?)\]\]></record>", re.S)
GTAPP_PAGE_RE = re.compile(r"page\('(\d+)'\)")

# 详情页正文候选容器，按优先级排列
CONTENT_SELECTORS = (
    ".wzzw-article",  # 泰州 CMS 详情页正文
    "#zoom",  # chinatax 税务平台
    'td[style*="line-height:28px"]',  # 省自然资源厅 gtapp 平台
    ".TRS_Editor",
    "#ivs_content",
)

# 聚合页导航/功能外链黑名单（不是政策文章）
EXTERNAL_NAV_DOMAINS = (
    "yjsgk.jsczt.cn",
    "wjk.jsrd.gov.cn",
    "www.jszwfw.gov.cn",
)


def new_session():
    session = CrawlerSession()
    session.headers.update(HEADERS)
    return session


def fetch_soup(session, url, timeout=LIST_TIMEOUT):
    """GET 页面并返回 BeautifulSoup 对象，统一 UTF-8 解码。"""
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.text, "html.parser"), response.text


def extract_main_content(session, article_url, metrics):
    """提取详情页正文；失败时记录错误并返回空字符串。"""
    try:
        response = session.get(article_url, timeout=DETAIL_TIMEOUT)
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        for selector in CONTENT_SELECTORS:
            element = soup.select_one(selector)
            if element:
                text = element.get_text("\n", strip=True)
                if text:
                    return text
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


class _ScrapeContext:
    """一次栏目抓取过程的共享状态。"""

    def __init__(self, source_name, category, metrics=None):
        self.source_name = source_name
        self.category = category
        self.metrics = metrics or CrawlerMetrics()
        self.session = new_session()
        self.target_from, self.target_to = get_crawl_date_window()
        self.seen_urls = set()
        self.policies = []
        self.latest_candidates = []

    def add_record(self, title, url, pub_at):
        """登记一条候选记录；返回 True 表示落在目标窗口。"""
        metrics = self.metrics
        if not title or not url or not pub_at:
            metrics.invalid_item_count += 1
            return False
        if url in self.seen_urls:
            metrics.duplicate_policy_count += 1
            return False
        self.seen_urls.add(url)
        metrics.valid_item_count += 1
        self.latest_candidates.append({"title": title, "url": url, "pub_at": pub_at})
        if not is_target_date(pub_at, self.target_from, self.target_to):
            metrics.filtered_count += 1
            return False
        self.policies.append(
            {
                "title": title,
                "url": url,
                "pub_at": pub_at,
                "content": extract_main_content(self.session, url, metrics),
                "selected": False,
                "category": self.category,
                "source": self.source_name,
            }
        )
        return True

    def oldest_candidate_date(self):
        if not self.latest_candidates:
            return None
        return min(item["pub_at"] for item in self.latest_candidates)

    def finish(self):
        latest_sorted = sorted(
            self.latest_candidates, key=lambda x: x["pub_at"], reverse=True
        )
        latest_items = [
            {"title": item["title"], "pub_at": item["pub_at"]}
            for item in latest_sorted[:5]
        ]
        self.metrics.target_date_count = len(self.policies)
        self.metrics.empty_content_count = sum(
            1 for item in self.policies if not item.get("content")
        )
        return self.policies, latest_items, self.metrics


def _parse_li_clearfix(lis, base_url, metrics):
    """解析 li.clearfix 列表，逐条登记到 ctx。

    返回 [(title, url, pub_at)]（仅含三项均解析成功的条目）。
    """
    items = []
    for li in lis:
        li_text = li.get_text(" ", strip=True)
        if not DATE_RE.search(li_text):
            # 页头 mobile logo 等噪声 li，不算候选
            continue
        metrics.raw_item_count += 1
        link = li.select_one("a[href]")
        href = (link.get("href") or "").strip() if link else ""
        if not href or href.startswith(("javascript:", "#")):
            metrics.invalid_item_count += 1
            continue
        if href.startswith(("http://", "https://")):
            # 完整外链：排除导航/功能链接，保留政策文章外链（如公安部）
            if any(domain in href for domain in EXTERNAL_NAV_DOMAINS):
                metrics.invalid_item_count += 1
                continue
            article_url = href
        else:
            if not ART_HREF_RE.search(href):
                metrics.invalid_item_count += 1
                continue
            article_url = urljoin(base_url, href)

        title = (link.get("title") or "").strip() or link.get_text(" ", strip=True)
        date_match = DATE_RE.search(li_text)
        pub_at = parse_date(date_match.group(0)) if date_match else None
        if not title or not pub_at:
            metrics.invalid_item_count += 1
            continue
        items.append({"title": title, "url": article_url, "pub_at": pub_at})
    return items


def _parse_jpaas_li(html_fragment, base_url, metrics):
    """解析 jpaas 返回的 data.html 片段中的列表条目。

    各站模板不一致：多数为 ``li.clearfix``，工信局通知公告等栏目
    为 ``.page-content ul.lmy-list-ul > li``（无 class）。
    """
    soup = BeautifulSoup(html_fragment, "html.parser")
    lis = soup.select("li.clearfix")
    if not lis:
        lis = [
            li
            for li in soup.select(".page-content li")
            if li.select_one("a[href]") and DATE_RE.search(li.get_text(" ", strip=True))
        ]
    items = []
    for li in lis:
        metrics.raw_item_count += 1
        link = li.select_one("a[href]")
        href = (link.get("href") or "").strip() if link else ""
        if not href or href.startswith(("javascript:", "#")):
            metrics.invalid_item_count += 1
            continue
        if not href.startswith(("http://", "https://")) and not ART_HREF_RE.search(href):
            metrics.invalid_item_count += 1
            continue
        title = (link.get("title") or "").strip() or link.get_text(" ", strip=True)
        date_span = li.select_one("span.bt-right") or li.select_one("span")
        date_text = date_span.get_text(strip=True) if date_span else ""
        date_match = DATE_RE.search(date_text or li.get_text(" ", strip=True))
        pub_at = parse_date(date_match.group(0)) if date_match else None
        if not title or not pub_at:
            metrics.invalid_item_count += 1
            continue
        items.append(
            {"title": title, "url": urljoin(base_url, href), "pub_at": pub_at}
        )
    return items


def _extract_query_data(html):
    """从列表页 HTML 提取 (api_path, queryData dict)。"""
    match = QUERY_DATA_RE.search(html)
    if not match:
        return None, None
    raw = match.group(1).replace("'", '"')
    try:
        query_data = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    api_match = re.search(r'url="(/api-gateway/[^"]+)"', html)
    api_path = api_match.group(1) if api_match else JPAAS_API_PATH
    return api_path, query_data


def _scrape_jpaas_pages(ctx, page_url, query_data, api_path):
    """对单个 jpaas 栏目翻页抓取，直到页内最大日期早于窗口起点。"""
    metrics = ctx.metrics
    api_url = urljoin(page_url, api_path)
    page_no = 1
    rows = 15
    total = None
    while page_no <= MAX_PAGES:
        params = dict(query_data)
        if page_no > 1:
            params["paramJson"] = json.dumps({"pageNo": page_no, "pageSize": rows})
        try:
            response = ctx.session.get(api_url, params=params, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {api_url} page={page_no} - {exc}")
            break
        if not payload.get("success"):
            metrics.errors.append(
                f"列表API返回失败: {api_url} page={page_no} - {payload.get('message')}"
            )
            break
        html_fragment = (payload.get("data") or {}).get("html") or ""
        if page_no == 1:
            page_div = re.search(
                r'class="pagination"[^>]*rows="(\d+)"[^>]*count="(\d+)"', html_fragment
            )
            if page_div:
                rows = int(page_div.group(1)) or rows
                total = int(page_div.group(2))
            else:
                rows_match = re.search(r'rows="(\d+)"', html_fragment)
                count_match = re.search(r'count="(\d+)"', html_fragment)
                if rows_match:
                    rows = int(rows_match.group(1)) or rows
                if count_match:
                    total = int(count_match.group(1))

        items = _parse_jpaas_li(html_fragment, page_url, metrics)
        page_dates = []
        for item in items:
            added = ctx.add_record(item["title"], item["url"], item["pub_at"])
            if added or item["url"] in ctx.seen_urls:
                page_dates.append(item["pub_at"])

        if not items:
            break
        newest_on_page = max(page_dates) if page_dates else None
        if newest_on_page and newest_on_page < ctx.target_from:
            break
        if total is not None and page_no * rows >= total:
            break
        page_no += 1


def _scrape_column_url(ctx, column_url, allow_subcolumns=True):
    """抓取任意栏目页：自动识别 jpaas / 聚合页 / 静态列表。"""
    metrics = ctx.metrics
    try:
        soup, html = fetch_soup(ctx.session, column_url)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {column_url} - {exc}")
        return

    api_path, query_data = _extract_query_data(html)
    more_links = [
        urljoin(column_url, a.get("href"))
        for a in soup.select("a.xxgk_more[href]")
        if a.get("href")
    ]
    year_archive_links = [u for u in more_links if YEAR_ARCHIVE_RE.search(u)]
    subcolumn_links = [u for u in more_links if not YEAR_ARCHIVE_RE.search(u)]

    if query_data and not soup.select(".xxgk-zcnr li.clearfix"):
        # 纯 jpaas 动态列表
        _scrape_jpaas_pages(ctx, column_url, query_data, api_path)
        return

    # 静态部分：聚合页（.xxgk-zcnr）或静态列表（li.clearfix）
    scope = soup.select_one(".xxgk-zcnr") or soup
    items = _parse_li_clearfix(scope.select("li.clearfix"), column_url, metrics)
    for item in items:
        ctx.add_record(item["title"], item["url"], item["pub_at"])

    if not allow_subcolumns:
        return

    # 递归抓子栏目（部门文件 / 部门政策解读 jpaas 列表）
    for sub_url in dict.fromkeys(subcolumn_links):
        _scrape_column_url(ctx, sub_url, allow_subcolumns=False)

    # 年度归档：仅在当前数据未覆盖窗口起点时按年回溯
    for archive_url in dict.fromkeys(year_archive_links):
        oldest = ctx.oldest_candidate_date()
        if oldest is not None and oldest <= ctx.target_from:
            break
        _scrape_column_url(ctx, archive_url, allow_subcolumns=False)


def scrape_taizhou_column(list_url, source_name, category):
    """抓取泰州 CMS 栏目页，返回 (policies, latest_items, metrics)。"""
    ctx = _ScrapeContext(source_name, category)
    _scrape_column_url(ctx, list_url, allow_subcolumns=True)
    return ctx.finish()


def scrape_gtapp_site(list_url, source_name, category):
    """抓取省自然资源厅 gtapp 平台栏目（POST cpage 分页）。"""
    ctx = _ScrapeContext(source_name, category)
    metrics = ctx.metrics

    page_no = 1
    total_pages = 1
    while page_no <= total_pages and page_no <= MAX_PAGES:
        try:
            if page_no == 1:
                soup, html = fetch_soup(ctx.session, list_url)
            else:
                response = ctx.session.post(
                    list_url, data={"cpage": str(page_no)}, timeout=LIST_TIMEOUT
                )
                response.raise_for_status()
                response.encoding = "utf-8"
                html = response.text
                soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {list_url} page={page_no} - {exc}")
            break

        if page_no == 1:
            page_nums = [int(n) for n in GTAPP_PAGE_RE.findall(html)]
            total_pages = max(page_nums) if page_nums else 1

        containers = soup.select("td.nlist")
        metrics.raw_item_count += len(containers)
        page_dates = []
        for container in containers:
            link = container.select_one("a[href]")
            href = (link.get("href") or "").strip() if link else ""
            title = (link.get("title") or "").strip() if link else ""
            if not title and link:
                title = link.get_text(" ", strip=True)
            date_match = DATE_RE.search(container.get_text(" ", strip=True))
            pub_at = parse_date(date_match.group(0)) if date_match else None
            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue
            article_url = urljoin(list_url, href.replace("&amp;", "&"))
            added = ctx.add_record(title, article_url, pub_at)
            if added or article_url in ctx.seen_urls:
                page_dates.append(pub_at)

        if not containers:
            break
        if page_dates and max(page_dates) < ctx.target_from:
            break
        page_no += 1

    return ctx.finish()


def scrape_chinatax_column(list_url, source_name, category):
    """抓取省税务局 chinatax 平台栏目（dataproxy.jsp XML 分页）。"""
    ctx = _ScrapeContext(source_name, category)
    metrics = ctx.metrics

    try:
        _, html = fetch_soup(ctx.session, list_url)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {list_url} - {exc}")
        return ctx.finish()

    next_match = NEXTGROUP_RE.search(html)
    if not next_match:
        metrics.errors.append(f"列表页未找到分页接口: {list_url}")
        return ctx.finish()
    next_url = urljoin(list_url, next_match.group(1).replace("&amp;", "&"))

    page_no = 1
    while page_no <= MAX_PAGES:
        page_url = re.sub(r"page=\d+", f"page={page_no}", next_url, count=1)
        try:
            response = ctx.session.get(page_url, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            response.encoding = "utf-8"
            xml_text = response.text
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {page_url} - {exc}")
            break

        records = RECORD_RE.findall(xml_text)
        if not records:
            break
        page_dates = []
        for record in records:
            fragment = BeautifulSoup(record, "html.parser")
            li = fragment.select_one("li")
            if not li:
                continue
            metrics.raw_item_count += 1
            link = li.select_one("a[href]")
            href = (link.get("href") or "").strip() if link else ""
            title = (link.get("title") or "").strip() if link else ""
            if not title and link:
                title = link.get_text(" ", strip=True)
            date_match = DATE_RE.search(li.get_text(" ", strip=True))
            pub_at = parse_date(date_match.group(0)) if date_match else None
            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue
            article_url = urljoin(list_url, href)
            added = ctx.add_record(title, article_url, pub_at)
            if added or article_url in ctx.seen_urls:
                page_dates.append(pub_at)

        if page_dates and max(page_dates) < ctx.target_from:
            break
        if not NEXTGROUP_RE.search(xml_text):
            break
        page_no += 1

    return ctx.finish()
