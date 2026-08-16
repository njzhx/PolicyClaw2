# -*- coding: utf-8 -*-
"""淮安市系列爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 City 目录下的淮安各单站爬虫复用：

1. ``scrape_lb_site``：适用于服务端渲染的栏目列表页
   （``/col/xxxx/index.html`` 与 ``xxx/list.html`` 两种模板），
   列表结构为 ``li``/``div.lbwz`` 内 ``div.lb-time``/``span.lb-time``/
   ``div.time`` 日期 + ``a[title]`` 链接，分页 URL 形如 ``index_2.html``；
2. ``scrape_xxgk_site``：适用于政府信息公开平台
   （``/cmsweb/zwgk/sj/index.html`` 与 ``indexdept.html``），
   数据通过 ``POST /articleCommonController/lists.do`` JSON 接口获取；
3. ``extract_main_content``：详情页正文提取，兼容 ``#zoom``、
   ``#artical``、``.nr-zw``、``.nrwz``、``.wz3`` 等模板。
"""

import math
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from crawler_core import get_crawl_date_window, is_target_date, parse_date


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 30

# 文章详情链接特征：路径中包含数字 ID 目录（如 /17855136/、/202011/）
# 或长数字文件名（如 /1605149651006VPmeeson.html）
ARTICLE_HREF_RE = re.compile(r"/\d{6,}/|/\d{10,}")
DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
NSETPAGE_RE = re.compile(r"nsetpage\(\s*\d+\s*,\s*(\d+)\s*,\s*(\d+)")

# 正文容器候选选择器，按优先级排列
CONTENT_SELECTORS = (
    "#zoom",
    "#artical",
    ".nr-zw",
    ".nrwz",
    ".wz3",
    ".TRS_Editor",
    "#ivs_content",
    'td[style*="line-height:28px"]',
)

# 列表条目容器（覆盖淮安各站栏目页模板）
ITEM_CONTAINER_CSS = (
    "div.lb-lb ul li, "
    "div.list-lb ul li, "
    "div.list-r ul li, "
    "div.lb_zw ul li, "
    "div.nylb div.lbwz, "
    "td.nlist"
)


def new_session():
    session = requests.Session()
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


def _parse_total_pages(html):
    """从 nsetpage(1,total,size,...) 计算总页数。"""
    match = NSETPAGE_RE.search(html)
    if not match:
        return 1
    total, size = int(match.group(1)), int(match.group(2))
    if size <= 0:
        return 1
    return max(1, math.ceil(total / size))


def _page_url(list_url, page_index):
    """index.html -> index_2.html；list.html -> list_2.html。"""
    if page_index == 1:
        return list_url
    base, sep, ext = list_url.rpartition(".")
    if not sep:
        return list_url
    return f"{base}_{page_index}.{ext}"


def _select_containers(soup):
    """选中列表条目容器；模板选择器未命中时回退到含日期的 li。"""
    containers = soup.select(ITEM_CONTAINER_CSS)
    if not containers:
        containers = [
            li for li in soup.select("li") if DATE_RE.search(li.get_text(" ", strip=True))
        ]
    return containers


def _parse_list_items(soup, base_url, metrics, containers, seen_urls):
    """解析一页列表，返回 [{title, url, pub_at}]（保持页面顺序）。"""
    items = []
    for container in containers:
        link = container.select_one("a[href]")
        if not link:
            metrics.invalid_item_count += 1
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#")):
            metrics.invalid_item_count += 1
            continue
        if not ARTICLE_HREF_RE.search(href):
            metrics.invalid_item_count += 1
            continue

        title = (link.get("title") or "").strip() or link.get_text(" ", strip=True)
        date_match = DATE_RE.search(container.get_text(" ", strip=True))
        pub_at = parse_date(date_match.group(0)) if date_match else None

        if not title or not pub_at:
            metrics.invalid_item_count += 1
            continue

        article_url = urljoin(base_url, href)
        if article_url in seen_urls:
            metrics.duplicate_policy_count += 1
            continue
        seen_urls.add(article_url)
        metrics.valid_item_count += 1
        items.append({"title": title, "url": article_url, "pub_at": pub_at})
    return items


def scrape_lb_site(list_url, source_name, category, metrics=None):
    """抓取服务端渲染栏目列表页（col/index.html 与 list.html 模板）。"""
    policies = []
    latest_candidates = []
    if metrics is None:
        from crawler_core import CrawlerMetrics

        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    total_pages = 1
    page_index = 1
    seen_urls = set()
    while page_index <= total_pages and page_index <= MAX_PAGES:
        page_url = _page_url(list_url, page_index)
        try:
            soup, html = fetch_soup(session, page_url)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
            break

        if page_index == 1:
            total_pages = _parse_total_pages(html)

        containers = _select_containers(soup)
        metrics.raw_item_count += len(containers)
        duplicates_before = metrics.duplicate_policy_count
        items = _parse_list_items(soup, list_url, metrics, containers, seen_urls)
        if not containers and not items:
            metrics.errors.append(f"列表页未解析到条目: {page_url}")
        elif not items and metrics.duplicate_policy_count > duplicates_before:
            metrics.errors.append(f"列表页重复，已停止翻页: {page_url}")

        for item in items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "pub_at": item["pub_at"],
                    "content": extract_main_content(session, item["url"], metrics),
                    "selected": False,
                    "category": category,
                    "source": source_name,
                }
            )

        if not items:
            break
        # 列表按发布日期倒序（可能含置顶）：整页都早于窗口起点时才停止翻页
        newest_on_page = max(item["pub_at"] for item in items)
        if newest_on_page < target_from:
            break
        page_index += 1

    latest_sorted = sorted(latest_candidates, key=lambda x: x["pub_at"], reverse=True)
    latest_items = [
        {"title": item["title"], "pub_at": item["pub_at"]} for item in latest_sorted[:5]
    ]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items, metrics


def scrape_xxgk_site(api_host, source_name, category, topic="", deptid="", rdeptid="", metrics=None):
    """抓取政府信息公开平台（POST /articleCommonController/lists.do）。"""
    policies = []
    latest_candidates = []
    if metrics is None:
        from crawler_core import CrawlerMetrics

        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    api_url = urljoin(api_host, "/articleCommonController/lists.do")

    page_index = 1
    page_size = 50
    seen_urls = set()
    while page_index <= MAX_PAGES:
        post_data = {
            "page": page_index,
            "pagesize": page_size,
            "topic": topic,
            "title": "",
            "docNo": "",
            "orgname": "",
            "summary": "",
            "key": "",
        }
        if deptid:
            post_data["deptid"] = deptid
        if rdeptid:
            post_data["rdeptid"] = rdeptid

        try:
            response = session.post(api_url, data=post_data, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {api_url} - {exc}")
            break

        value = payload.get("value") or {}
        records = value.get("list") or []
        metrics.raw_item_count += len(records)

        page_items = []
        duplicates_before = metrics.duplicate_policy_count
        for record in records:
            title = str(record.get("title") or "").strip()
            link = str(record.get("link") or "").strip()
            path = str(record.get("path") or "").strip()
            domain = str(record.get("domain") or "").strip()
            pub_at = parse_date(record.get("releaseTime"))

            article_url = link or urljoin(domain or api_host, path)
            if not title or not article_url or not pub_at:
                metrics.invalid_item_count += 1
                continue
            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)
            metrics.valid_item_count += 1
            page_items.append({"title": title, "url": article_url, "pub_at": pub_at})

        if records and not page_items and metrics.duplicate_policy_count > duplicates_before:
            metrics.errors.append(
                f"列表 API 返回重复页，已停止翻页: page={page_index}"
            )

        for item in page_items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "pub_at": item["pub_at"],
                    "content": extract_main_content(session, item["url"], metrics),
                    "selected": False,
                    "category": category,
                    "source": source_name,
                }
            )

        if not page_items:
            break
        newest_on_page = max(item["pub_at"] for item in page_items)
        if newest_on_page < target_from:
            break
        total = int(value.get("total") or 0)
        if page_index * page_size >= total:
            break
        page_index += 1

    latest_sorted = sorted(latest_candidates, key=lambda x: x["pub_at"], reverse=True)
    latest_items = [
        {"title": item["title"], "pub_at": item["pub_at"]} for item in latest_sorted[:5]
    ]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items, metrics
