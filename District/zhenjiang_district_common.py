# -*- coding: utf-8 -*-
"""镇江市区县级爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 District 目录下的镇江各区县爬虫复用。

镇江各区县网站使用与市级相同的"政府信息公开"模板，但列表容器
选择器略有差异：市级为 ``ul.pageList.newsList > li``，区县多为
``div.listContent.newsList > li``。本模块兼容两种形态。

三种页面类型：
1. 标准列表页（zfwj/zfbwj）：``div.listContent.newsList`` 或
   ``ul.pageList.newsList`` + ``createPageHTML`` 分页；
2. 子栏目导航页（句容 zfwj navs）：页面上有多个子栏目链接，
   需聚合抓取各子栏目；
3. 部门发文导航页（各区县 bmwj）：页面上列各部门链接，需逐个
   进入部门页面提取文件。
"""

import re
import os
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from crawler_http import CrawlerSession
from db_utils import save_to_policy


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 60

LIST_ITEM_CSS = (
    "ul.pageList.newsList li, "
    "div.listContent.newsList li, "
    "div.pageList > ul > li, "
    "table.gzktable tbody tr"
)

CREATE_PAGE_RE = re.compile(
    r"createPageHTML\('[^']*',\s*(\d+)\s*,\s*\d+\s*,\s*'([^']+)',\s*'([^']+)'"
)

CONTENT_SELECTORS = (
    "div.article-content#zoomcon",
    "#zoomcon",
    "div.article-content",
    ".xxgk-tt-content",
    ".zoom",
    ".main-txt",
    ".TRS_Editor",
    "#zoom",
)

DEPT_LINK_CSS = (
    "ul.xxgks-list a[href]",
    "ul.infoList.notTime a[href]",
    "div.pageListCols a[href]",
)

META_REFRESH_RE = re.compile(
    r'url\s*=\s*["\']?([^"\'>\s]+)', re.IGNORECASE
)


def new_session():
    session = CrawlerSession()
    session.headers.update(HEADERS)
    session.trust_env = False
    return session


def fetch_text(session, url, timeout=LIST_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def extract_content(session, article_url, metrics, response=None):
    try:
        if response is None:
            response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in CONTENT_SELECTORS:
            original = soup.select_one(selector)
            if original is None:
                continue
            # Work on a copy so overlapping fallback containers remain intact.
            element = BeautifulSoup(str(original), "html.parser")
            for extra in element.select("script, style"):
                extra.decompose()
            has_media = bool(element.select("img, object, embed"))
            for link in element.select("a[href]"):
                path = link.get("href", "").split("?", 1)[0].split("#", 1)[0].lower()
                if path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")):
                    has_media = True
                    link.decompose()
            text = element.get_text("\n", strip=True)
            if text:
                return text
            media_only = media_only or has_media
            if has_media:
                break
        # Dantu publishes file-only material in a separate verified appendix block.
        if not media_only and soup.select_one(".article-appendixs.rel-appendixs a[href]"):
            media_only = True
        # Jiangsu natural-resources disclosure pages place attachments outside
        # the otherwise empty article body.
        if not media_only and soup.select_one('a[href*="/gtapp/nrgl/GJAttach/"]'):
            media_only = True
        if media_only:
            desc = soup.select_one('meta[name="Description"], meta[name="description"]')
            summary = str(desc.get("content") or "").strip() if desc else ""
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            title_meta = soup.select_one('meta[name="ArticleTitle"]')
            article_title = str(title_meta.get("content") or "").strip() if title_meta else ""
            if summary and summary not in {title, article_title}:
                return summary
            metrics.errors.append(f"[ATTACHMENT_ONLY] 图片/附件型页面无可提取网页正文: {article_url}")
        else:
            metrics.errors.append(f"[CONTENT_MISSING] 正文选择器未命中或正文为空: {article_url}")
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _page_url(list_url, page_index, prefix, suffix):
    base = list_url.rsplit("/", 1)[0]
    if page_index <= 1:
        return f"{base}/{prefix}.{suffix}"
    return f"{base}/{prefix}_{page_index}.{suffix}"


def _parse_list_items(soup, list_url):
    records = []
    oldest_date = None
    for node in soup.select(LIST_ITEM_CSS):
        link = node.find("a")
        if not link:
            continue
        title = (link.get("title") or link.get_text(" ", strip=True) or "").strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        time_node = node.find("span", class_="time")
        pub_at = parse_date(time_node.get_text(strip=True)) if time_node else None
        records.append(
            {
                "title": title,
                "url": urljoin(list_url, href),
                "pub_at": pub_at,
            }
        )
        if pub_at and (oldest_date is None or pub_at < oldest_date):
            oldest_date = pub_at
    return records, oldest_date


def scrape_channel(session, channel_url, target_from, target_to, metrics,
                   policies, latest_items, seen_urls, first_html=None):
    try:
        if first_html is None:
            first_html = fetch_text(session, channel_url, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {channel_url} - {exc}")
        return False

    first_soup = BeautifulSoup(first_html, "html.parser")
    page_match = CREATE_PAGE_RE.search(first_html)
    total_pages = int(page_match.group(1)) if page_match else 1
    prefix = page_match.group(2) if page_match else ""
    suffix = page_match.group(3) if page_match else "shtml"
    advertised_pages = total_pages
    total_pages = min(total_pages, MAX_PAGES)

    page_index = 1
    consecutive_empty_pages = 0
    while page_index <= total_pages:
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 运行预算不足: {channel_url}")
            break
        if page_index == 1:
            soup = first_soup
            page_url = channel_url
        else:
            page_url = _page_url(channel_url, page_index, prefix, suffix)
            try:
                html = fetch_text(session, page_url, timeout=LIST_TIMEOUT)
            except Exception as exc:
                metrics.errors.append(f"列表分页抓取失败: {page_url} - {exc}")
                break
            soup = BeautifulSoup(html, "html.parser")

        records, oldest_date = _parse_list_items(soup, page_url)
        metrics.raw_item_count += len(records)

        if not records:
            consecutive_empty_pages += 1
            if page_index == 1:
                metrics.errors.append(f"列表页未解析到记录，停止翻页: {page_url}")
                break
            if consecutive_empty_pages >= 2:
                metrics.errors.append(
                    f"连续 {consecutive_empty_pages} 页未解析到记录，停止翻页: {page_url}"
                )
                break
        else:
            consecutive_empty_pages = 0

        for record in records:
            if "/dantu/quz/" in record["url"]:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"[NON_ARTICLE] 区领导个人资料页不作为文章: {record['url']}")
                continue
            detail_response = None
            if not record["pub_at"]:
                try:
                    detail_response = session.get(record["url"], headers=HEADERS, timeout=DETAIL_TIMEOUT)
                    detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.content, "html.parser")
                    date_meta = detail_soup.select_one('meta[name="PubDate"]')
                    record["pub_at"] = parse_date(date_meta.get("content")) if date_meta else None
                except Exception as exc:
                    metrics.errors.append(f"详情发布日期抓取失败: {record['url']} - {exc}")
            if not record["pub_at"]:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录日期缺失或无效: {record['url']}")
                continue
            if record["url"] in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(record["url"])
            metrics.valid_item_count += 1
            if page_index == 1:
                latest_items.append(
                    {"title": record["title"], "pub_at": record["pub_at"]}
                )
            if not is_target_date(record["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                {
                    "title": record["title"],
                    "url": record["url"],
                    "pub_at": record["pub_at"],
                    "content": extract_content(session, record["url"], metrics, response=detail_response),
                    "selected": False,
                    "category": None,
                    "source": None,
                }
            )

        if records and all(record["pub_at"] and record["pub_at"] < target_from for record in records):
            break
        page_index += 1

    if page_index > total_pages and advertised_pages > total_pages:
        metrics.errors.append("[PAGINATION_INCOMPLETE] 达到安全页数上限")
    return True


def scrape_channels(source_name, channel_urls, category):
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    for channel_url in channel_urls:
        list_ok = scrape_channel(
            session, channel_url, target_from, target_to,
            metrics, policies, latest_items, seen_urls,
        )
        if not list_ok:
            metrics.errors.append(
                f"列表页请求失败，跳过后续同站栏目（起于: {channel_url}）"
            )
            break

    for item in policies:
        item["source"] = source_name
        item["category"] = category

    latest_items = sorted(
        latest_items, key=lambda x: x["pub_at"], reverse=True
    )[:5]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items, metrics


def _resolve_redirect(session, url, metrics):
    """如果页面是 meta refresh 重定向页，返回真实 URL；否则返回 None。"""
    try:
        text = fetch_text(session, url, timeout=DETAIL_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"部门页面请求失败: {url} - {exc}")
        return None
    if len(text) < 2000:
        soup = BeautifulSoup(text, "html.parser")
        meta = soup.find("meta", attrs={"http-equiv": "refresh"})
        if meta:
            content = meta.get("content", "")
            match = re.search(
                r'url\s*=\s*[\'"]?([^\'">\s]+)', content, re.IGNORECASE
            )
            if match:
                return urljoin(url, match.group(1))
    return None


def _extract_dept_links(soup, nav_url):
    """从部门导航页提取部门链接。"""
    links = []
    seen = set()
    for css in DEPT_LINK_CSS:
        for a in soup.select(css):
            href = (a.get("href") or "").strip()
            text = a.get_text(" ", strip=True)
            if not href or href in ("javascript:void(0)", "#", ""):
                continue
            if href in seen:
                continue
            if not text or len(text) > 30:
                continue
            # The directory also exposes the district/city government itself.
            # It is a separate government-file entry point, not a department.
            if text.endswith("人民政府") or text.endswith(("政府办公室", "党政办公室")):
                continue
            seen.add(href)
            links.append((text, urljoin(nav_url, href)))
        if links:
            break
    return links


def _aggregate_disclosure_url(department_url):
    """Return the verified per-department aggregate used by Runzhou/Yangzhong."""
    from urllib.parse import urlsplit
    parsed = urlsplit(department_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parsed.hostname == "www.runzhou.gov.cn":
        return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/fdzdgknr/rzxxgkpt_list.shtml"
    if parsed.hostname == "www.yz.gov.cn":
        return f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/fdzdgknr/yzxxgkpt_list.shtml"
    return None


def _scrape_yangzhong_natural_resources(session, target_from, target_to,
                                         metrics, policies, latest_items, seen_urls):
    """Scrape the official external disclosure table used by one Yangzhong bureau."""
    list_url = ("http://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
                "?classID=2c9082b55b6b7170015b6bb2889c008c&type=1")
    total_pages = 1
    for page in range(1, 101):
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 25 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 外部部门分页预算不足: {list_url}")
            return
        try:
            if page == 1:
                response = session.get(list_url, headers=HEADERS, timeout=LIST_TIMEOUT)
            else:
                response = session.post(list_url, data={"cpage": page}, headers=HEADERS, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            marker = re.search(r"当前\((\d+)/(\d+)\)页", response.text)
            if marker:
                total_pages = int(marker.group(2))
            rows = []
            for tr in soup.select("table.xxgk-listz tr"):
                link = tr.select_one('a[href*="messageID"]')
                cells = tr.select("td")
                if link and len(cells) >= 3:
                    rows.append((link, cells))
            if not rows:
                metrics.errors.append(f"列表页抓取失败: 未解析到自然资源部门记录 {list_url} page={page}")
                return
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {list_url} page={page} - {exc}")
            return
        metrics.raw_item_count += len(rows)
        page_dates = []
        for link, cells in rows:
            title = link.get_text(" ", strip=True)
            pub_at = parse_date(cells[-1].get_text(" ", strip=True))
            url = urljoin(list_url, link.get("href") or "")
            if not title or not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录标题或日期无效: {url}")
                continue
            page_dates.append(pub_at)
            if url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(url)
            metrics.valid_item_count += 1
            latest_items.append({"title": title, "pub_at": pub_at})
            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append({"title": title, "url": url, "pub_at": pub_at,
                             "content": extract_content(session, url, metrics),
                             "selected": False, "category": None, "source": None})
        if page >= total_pages:
            return
        if (len(page_dates) == len(rows) and page_dates == sorted(page_dates, reverse=True)
                and max(page_dates) < target_from):
            return
    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 外部部门达到页数上限: {list_url}")


def scrape_dept_navigation(source_name, nav_url, category):
    """抓取部门发文导航页，逐个温和进入部门页面提取文件。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    try:
        nav_html = fetch_text(session, nav_url, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"导航页抓取失败: {nav_url} - {exc}")
        return policies, latest_items, metrics

    nav_soup = BeautifulSoup(nav_html, "html.parser")
    dept_links = _extract_dept_links(nav_soup, nav_url)
    # Department navigation links are not article candidates.

    if not dept_links:
        metrics.errors.append(f"导航页未提取到部门链接: {nav_url}")
        return policies, latest_items, metrics

    for dept_name, dept_url in dept_links:
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {nav_url}")
            break
        # Reuse the department response instead of requesting every list twice.
        try:
            first_html = fetch_text(session, dept_url)
            real_url = dept_url
            soup = BeautifulSoup(first_html, "html.parser")
            meta = soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
            match = META_REFRESH_RE.search(meta.get("content", "")) if meta else None
            if match:
                real_url = urljoin(dept_url, match.group(1))
                first_html = fetch_text(session, real_url)
        except Exception as exc:
            metrics.errors.append(f"部门页抓取失败: {dept_url} - {exc}")
            continue
        if urlsplit(real_url).hostname == "zrzy.jiangsu.gov.cn":
            _scrape_yangzhong_natural_resources(
                session, target_from, target_to, metrics,
                policies, latest_items, seen_urls,
            )
            continue
        if _parse_list_items(BeautifulSoup(first_html, "html.parser"), real_url)[0]:
            channels = [(real_url, first_html)]
        else:
            # Department directory targets may be disclosure guides. Follow only verified
            # article-column labels, never treat the guide itself as a policy list.
            soup = BeautifulSoup(first_html, "html.parser")
            aggregate_url = _aggregate_disclosure_url(real_url)
            if aggregate_url:
                try:
                    aggregate_html = fetch_text(session, aggregate_url)
                    aggregate_soup = BeautifulSoup(aggregate_html, "html.parser")
                    aggregate_title = aggregate_soup.title.get_text(" ", strip=True) if aggregate_soup.title else ""
                    if "法定主动公开内容" in aggregate_title and _parse_list_items(aggregate_soup, aggregate_url)[0]:
                        channels = [(aggregate_url, aggregate_html)]
                    else:
                        channels = []
                except Exception as exc:
                    metrics.errors.append(f"部门聚合列表页抓取失败: {aggregate_url} - {exc}")
                    channels = []
            else:
                channels = []
            labels = {"部门文件", "政策文件", "通知公告", "政策解读", "工作安排", "工作动态", "文件下载"}
            if not channels:
                for link in soup.select("a[href]"):
                    label = "".join(link.get_text().split())
                    href = urljoin(real_url, link["href"])
                    if label in labels and urlsplit(href).hostname == urlsplit(real_url).hostname:
                        if href not in [url for url, _ in channels]:
                            channels.append((href, None))
            if not channels:
                metrics.errors.append(f"[CHANNEL_MISMATCH] 部门入口不是文章列表，未找到已验证的文章栏目: {real_url}")
                continue
        for channel_url, channel_html in channels:
            scrape_channel(session, channel_url, target_from, target_to,
                           metrics, policies, latest_items, seen_urls, first_html=channel_html)

    for item in policies:
        item["source"] = source_name
        item["category"] = category

    latest_items = sorted(
        latest_items, key=lambda x: x["pub_at"], reverse=True
    )[:5]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items, metrics


def run_channel_crawler(source_name, channel_urls, category):
    data, latest_items, metrics = scrape_channels(
        source_name, channel_urls, category
    )
    processed_items, api_push_result = save_to_policy(data, source_name)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )


def run_dept_crawler(source_name, nav_url, category):
    data, latest_items, metrics = scrape_dept_navigation(
        source_name, nav_url, category
    )
    processed_items, api_push_result = save_to_policy(data, source_name)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )
