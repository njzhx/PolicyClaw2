# -*- coding: utf-8 -*-
"""宿迁市区县级爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 District 目录下的宿迁各区县爬虫复用。

宿迁各区县网站使用统一的"政府信息公开"模板，两种列表形态：

1. 标准列表页（zfwj/zfbwj）：``ul.listContent > li > a[title] + span``
   （日期 YYYY-MM-DD），分页 URL 为 ``前缀_N.shtml``；
2. 规范性文件列表页（bmwj/xzgfxwj）：``ul.gzklist > li.flex.btime``
   + ``a[title]``，日期从 ``li[stime]`` 属性提取（不从 URL 年月虚构发布日期）。

两种形态均使用 ``createPageHTML`` 生成静态分页。
"""

import re
import os
import time
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


def _parse_standard_items(soup, list_url):
    """解析 ul.listContent 标准列表条目。"""
    records = []
    oldest_date = None
    for li in soup.select("ul.listContent > li"):
        link = li.find("a", href=True)
        if not link:
            continue
        title = (link.get("title") or link.get_text(" ", strip=True) or "").strip()
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
        records.append({
            "title": title,
            "url": urljoin(list_url, href),
            "pub_at": pub_at,
        })
        if pub_at and (oldest_date is None or pub_at < oldest_date):
            oldest_date = pub_at
    return records, oldest_date


def _parse_gfxwj_items(soup, list_url):
    """解析 ul.gzklist > li.flex.btime 规范性文件条目。

    日期从 li[stime] 或列表显示的完整日期提取；不从 URL 虚构日期。
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
        title = (link.get("title") or link.get_text(" ", strip=True) or "").strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue

        pub_at = None
        stime = li.get("stime", "").strip()
        if stime:
            pub_at = parse_date(stime)
        if not pub_at:
            m = DATE_RE.search(li.get_text(" ", strip=True))
            if m:
                pub_at = parse_date(m.group(0))

        records.append({
            "title": title,
            "url": urljoin(list_url, href),
            "pub_at": pub_at,
        })
        if pub_at and (oldest_date is None or pub_at < oldest_date):
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
    advertised_pages = total_pages
    total_pages = min(total_pages, MAX_PAGES)

    page_index = 1
    consecutive_empty = 0
    while page_index <= total_pages:
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 运行预算不足: {list_url}")
            break
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
                metrics.errors.append(f"无法解析发布日期: {record['url']}")
                continue
            if record["url"] in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(record["url"])
            # Verified normative-document placeholder: an empty article body or
            # a body repeating only the no-document notice, with no attachment.
            if "未制发行政规范性文件" in record["title"]:
                try:
                    if detail_response is None:
                        detail_response = session.get(record["url"], headers=HEADERS, timeout=DETAIL_TIMEOUT)
                        detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.content, "html.parser")
                    body = detail_soup.select_one("#zoomcon ucapcontent")
                    if (body is not None and not body.select("img, object, embed, a[href]")
                            and body.get_text(" ", strip=True) in {"", record["title"].strip()}):
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"[NON_ARTICLE] 已核实行政规范性文件栏目未更新占位页: {record['url']}")
                        continue
                except Exception as exc:
                    metrics.errors.append(f"占位页面核验失败，保留记录继续处理: {record['url']} - {exc}")
            metrics.valid_item_count += 1
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
                "content": extract_content(session, record["url"], metrics, response=detail_response),
                "selected": False,
                "category": None,
                "source": None,
            })

        if records and all(record["pub_at"] and record["pub_at"] < target_from for record in records):
            break
        page_index += 1

    if page_index > total_pages and advertised_pages > total_pages:
        metrics.errors.append("[PAGINATION_INCOMPLETE] 达到安全页数上限")
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
