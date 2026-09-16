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
MAX_DEPARTMENTS = 8

LIST_ITEM_CSS = (
    "ul.pageList.newsList li, "
    "div.listContent.newsList li, "
    "div.pageList > ul > li"
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


def _parse_list_items(soup, list_url):
    records = []
    oldest_date = None
    for node in soup.select(LIST_ITEM_CSS):
        link = node.find("a")
        if not link:
            continue
        title = (link.get_text(" ", strip=True) or link.get("title") or "").strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        time_node = node.find("span", class_="time")
        pub_at = parse_date(time_node.get_text(strip=True)) if time_node else None
        if not pub_at:
            continue
        records.append(
            {
                "title": title,
                "url": urljoin(list_url, href),
                "pub_at": pub_at,
            }
        )
        if oldest_date is None or pub_at < oldest_date:
            oldest_date = pub_at
    return records, oldest_date


def scrape_channel(session, channel_url, target_from, target_to, metrics,
                   policies, latest_items, seen_urls):
    try:
        first_html = fetch_text(session, channel_url, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {channel_url} - {exc}")
        return False

    first_soup = BeautifulSoup(first_html, "html.parser")
    page_match = CREATE_PAGE_RE.search(first_html)
    total_pages = int(page_match.group(1)) if page_match else 1
    prefix = page_match.group(2) if page_match else ""
    suffix = page_match.group(3) if page_match else "shtml"
    total_pages = min(total_pages, MAX_PAGES)

    page_index = 1
    consecutive_empty_pages = 0
    while page_index <= total_pages:
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
                    "content": extract_content(session, record["url"], metrics),
                    "selected": False,
                    "category": None,
                    "source": None,
                }
            )

        if oldest_date and oldest_date < target_from:
            break
        page_index += 1

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
            seen.add(href)
            links.append((text, urljoin(nav_url, href)))
        if links:
            break
    return links


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
    metrics.raw_item_count = len(dept_links)

    if not dept_links:
        metrics.errors.append(f"导航页未提取到部门链接: {nav_url}")
        return policies, latest_items, metrics

    for dept_name, dept_url in dept_links[:MAX_DEPARTMENTS]:
        real_url = dept_url
        redirect = _resolve_redirect(session, dept_url, metrics)
        if redirect:
            real_url = redirect

        list_ok = scrape_channel(
            session, real_url, target_from, target_to,
            metrics, policies, latest_items, seen_urls,
        )
        if not list_ok:
            continue

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
