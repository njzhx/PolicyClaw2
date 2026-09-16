# -*- coding: utf-8 -*-
"""宿迁市区县级爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 District 目录下的宿迁各区县爬虫复用。

宿迁各区县网站使用统一的"政府信息公开"模板，两种列表形态：

1. 标准列表页（zfwj/zfbwj）：``ul.listContent > li > a[title] + span``
   （日期 YYYY-MM-DD），分页 URL 为 ``前缀_N.shtml``；
2. 规范性文件列表页（bmwj/xzgfxwj）：``ul.gzklist > li.flex.btime``
   + ``a[title]``，日期从 ``li[stime]`` 属性提取（``stime``
   为空时从 href 路径 ``/YYYYMM/`` 推断年月）。

两种形态均使用 ``createPageHTML`` 生成静态分页。
"""

import re
from urllib.parse import urljoin

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

CREATE_PAGE_RE = re.compile(
    r"createPageHTML\('[^']*',\s*(\d+)\s*,\s*\d+\s*,\s*'([^']+)',\s*'([^']+)'"
)

CONTENT_SELECTORS = (
    "div.article-content#zoomcon",
    "#zoomcon",
    "div.article-content",
    ".zoom",
    ".main-txt",
    ".TRS_Editor",
    "#zoom",
)

DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
HREF_DATE_RE = re.compile(r"/(\d{4})(\d{2})/")


def new_session():
    session = CrawlerSession()
    session.headers.update(HEADERS)
    return session


def fetch_text(session, url, timeout=LIST_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def extract_content(session, article_url, metrics):
    try:
        text = fetch_text(session, article_url, timeout=DETAIL_TIMEOUT)
        soup = BeautifulSoup(text, "html.parser")
        for selector in CONTENT_SELECTORS:
            element = soup.select_one(selector)
            if not element:
                continue
            for tag in element.find_all(["script", "style"]):
                tag.decompose()
            content = element.get_text("\n", strip=True)
            if content:
                return content
        desc = soup.select_one('meta[name="Description"]')
        if desc and desc.get("content"):
            return desc["content"].strip()
        metrics.errors.append(f"正文选择器未命中: {article_url}")
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _page_url(list_url, page_index, prefix, suffix):
    base = list_url.rsplit("/", 1)[0]
    if page_index <= 1:
        return f"{base}/{prefix}.{suffix}"
    return f"{base}/{prefix}_{page_index}.{suffix}"


def _parse_standard_items(soup, list_url):
    """解析 ul.listContent 标准列表条目。"""
    records = []
    oldest_date = None
    for li in soup.select("ul.listContent > li"):
        link = li.find("a", href=True)
        if not link:
            continue
        title = (link.get_text(" ", strip=True) or link.get("title") or "").strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        date_text = ""
        for span in li.find_all("span"):
            text = span.get_text(strip=True)
            if DATE_RE.search(text):
                date_text = text
                break
        if not date_text:
            m = DATE_RE.search(li.get_text(" ", strip=True))
            if m:
                date_text = m.group(0)
        pub_at = parse_date(date_text) if date_text else None
        if not pub_at:
            continue
        records.append({
            "title": title,
            "url": urljoin(list_url, href),
            "pub_at": pub_at,
        })
        if oldest_date is None or pub_at < oldest_date:
            oldest_date = pub_at
    return records, oldest_date


def _parse_gfxwj_items(soup, list_url):
    """解析 ul.gzklist > li.flex.btime 规范性文件条目。

    日期优先从 li[stime] 属性提取；为空时从 href 路径 /YYYYMM/ 推断。
    """
    records = []
    oldest_date = None
    for li in soup.select("ul.gzklist > li"):
        h4 = li.select_one("div.bt h4") or li
        link = h4.find("a", href=True) if h4 else None
        if not link:
            link = li.find("a", href=True)
        if not link:
            continue
        title = (link.get_text(" ", strip=True) or link.get("title") or "").strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue

        pub_at = None
        stime = li.get("stime", "").strip()
        if stime:
            pub_at = parse_date(stime)
        if not pub_at:
            href_match = HREF_DATE_RE.search(href)
            if href_match:
                year, month = href_match.group(1), href_match.group(2)
                pub_at = parse_date(f"{year}-{month}-01")
        if not pub_at:
            m = DATE_RE.search(li.get_text(" ", strip=True))
            if m:
                pub_at = parse_date(m.group(0))
        if not pub_at:
            continue

        records.append({
            "title": title,
            "url": urljoin(list_url, href),
            "pub_at": pub_at,
        })
        if oldest_date is None or pub_at < oldest_date:
            oldest_date = pub_at
    return records, oldest_date


def _scrape_list(session, list_url, target_from, target_to, metrics,
                 policies, latest_items, seen_urls, parse_func):
    """通用分页抓取逻辑。"""
    try:
        first_html = fetch_text(session, list_url, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {list_url} - {exc}")
        return False

    first_soup = BeautifulSoup(first_html, "html.parser")
    page_match = CREATE_PAGE_RE.search(first_html)
    total_pages = int(page_match.group(1)) if page_match else 1
    prefix = page_match.group(2) if page_match else ""
    suffix = page_match.group(3) if page_match else "shtml"
    total_pages = min(total_pages, MAX_PAGES)

    page_index = 1
    consecutive_empty = 0
    while page_index <= total_pages:
        if page_index == 1:
            soup = first_soup
            page_url = list_url
        else:
            page_url = _page_url(list_url, page_index, prefix, suffix)
            try:
                html = fetch_text(session, page_url, timeout=LIST_TIMEOUT)
            except Exception as exc:
                metrics.errors.append(f"列表分页抓取失败: {page_url} - {exc}")
                break
            soup = BeautifulSoup(html, "html.parser")

        records, oldest_date = parse_func(soup, page_url)
        metrics.raw_item_count += len(records)

        if not records:
            consecutive_empty += 1
            if page_index == 1:
                metrics.errors.append(f"列表页未解析到记录: {page_url}")
                break
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0

        for record in records:
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
            policies.append({
                "title": record["title"],
                "url": record["url"],
                "pub_at": record["pub_at"],
                "content": extract_content(session, record["url"], metrics),
                "selected": False,
                "category": None,
                "source": None,
            })

        if oldest_date and oldest_date < target_from:
            break
        page_index += 1

    return True


def scrape_standard(source_name, list_url, category):
    """抓取标准列表页（ul.listContent），返回 (policies, latest_items, metrics)。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    _scrape_list(
        session, list_url, target_from, target_to,
        metrics, policies, latest_items, seen_urls, _parse_standard_items,
    )

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


def scrape_gfxwj(source_name, list_url, category):
    """抓取规范性文件列表页（ul.gzklist > li.flex.btime），返回三元组。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    _scrape_list(
        session, list_url, target_from, target_to,
        metrics, policies, latest_items, seen_urls, _parse_gfxwj_items,
    )

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


def run_standard_crawler(source_name, list_url, category):
    data, latest_items, metrics = scrape_standard(
        source_name, list_url, category
    )
    processed_items, api_push_result = save_to_policy(data, source_name)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )


def run_gfxwj_crawler(source_name, list_url, category):
    data, latest_items, metrics = scrape_gfxwj(
        source_name, list_url, category
    )
    processed_items, api_push_result = save_to_policy(data, source_name)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )
