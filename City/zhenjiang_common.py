# -*- coding: utf-8 -*-
"""镇江市系列爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 City 目录下的镇江各单站爬虫复用。

镇江市政府及各部门网站使用统一的"政府信息公开"模板：

1. 列表页为服务端渲染静态页，列表结构为
   ``ul.pageList.newsList > li`` 或 ``div.pageList > ul > li``，
   条目为 ``a[title]`` + ``span.time``（YYYY-MM-DD）；
2. 页面底部通过 ``createPageHTML('page_div', 总页数, 当前页,
   '文件名前缀', 'shtml', 总条数)`` 生成分页，第 N 页 URL 为
   ``前缀_N.shtml``（第 1 页为 ``前缀.shtml``）；
3. "文件/解读"类栏目页（``xxgk_lists.shtml``）本身无静态数据，
   实际列表在 ``xxgkbmwj``（部门文件）与 ``xxgkzcjd``（政策解读）
   两个子栏目的 ``xxgk_list.shtml``，由爬虫文件配置为多个频道聚合；
4. 详情页正文统一在 ``div.article-content#zoomcon``。
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerSession,
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
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

LIST_ITEM_CSS = "ul.pageList.newsList li, div.pageList > ul > li"
CREATE_PAGE_RE = re.compile(
    r"createPageHTML\('[^']*',\s*(\d+)\s*,\s*\d+\s*,\s*'([^']+)',\s*'([^']+)'"
)

CONTENT_SELECTORS = (
    "div.article-content#zoomcon",
    "#zoomcon",
    "div.article-content",
    ".xxgk-tt-content",
    ".TRS_Editor",
    "#zoom",
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
    """抓取详情页正文；失败记录错误并返回空字符串。"""
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
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _page_url(list_url, page_index, prefix, suffix):
    """根据 createPageHTML 的前缀构造第 N 页 URL。"""
    base = list_url.rsplit("/", 1)[0]
    if page_index <= 1:
        return f"{base}/{prefix}.{suffix}"
    return f"{base}/{prefix}_{page_index}.{suffix}"


def _parse_list_items(soup, list_url):
    """解析一页的列表条目，返回 (records, oldest_date)。

    records: [{"title": ..., "url": ..., "pub_at": date}]
    """
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
    """抓取单个栏目列表（含分页），把目标日期数据追加到 policies。"""
    try:
        first_html = fetch_text(session, channel_url, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {channel_url} - {exc}")
        return

    first_soup = BeautifulSoup(first_html, "html.parser")
    page_match = CREATE_PAGE_RE.search(first_html)
    total_pages = int(page_match.group(1)) if page_match else 1
    prefix = page_match.group(2) if page_match else ""
    suffix = page_match.group(3) if page_match else "shtml"
    total_pages = min(total_pages, MAX_PAGES)

    page_index = 1
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
                    "category": "镇江",
                    "source": None,  # 由调用方统一填充
                }
            )

        # 列表按发布日期倒序，本页最旧日期已早于目标窗口则停止翻页
        if oldest_date and oldest_date < target_from:
            break
        page_index += 1


def scrape_channels(source_name, channel_urls, category="镇江"):
    """抓取一个或多个栏目并汇总，返回 (policies, latest_items, metrics)。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    for channel_url in channel_urls:
        scrape_channel(
            session,
            channel_url,
            target_from,
            target_to,
            metrics,
            policies,
            latest_items,
            seen_urls,
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


def run_crawler(source_name, channel_urls, category="镇江"):
    """统一执行抓取与保存，返回 CrawlerRunResult。"""
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
