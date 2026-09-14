"""
苏州市相城区_政府办发文爬虫
目标栏目：http://www.szxc.gov.cn/szxcrmzf/zcwj/xcxxgkml_zcwj.shtml
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "http://www.szxc.gov.cn/szxcrmzf/zcwj/xcxxgkml_zcwj.shtml"
SOURCE_NAME = "苏州市相城区_政府办发文"
CATEGORY = "苏州_相城区"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".wenZhang")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one(".pages_content")
            or soup.select_one("#zoom")
            or soup.select_one(".xlxlcont")
            or soup.select_one(".Custom_UnionStyle")
            or soup.select_one(".article")
            or soup.select_one(".content")
            or soup.select_one(".view-content")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_items(soup):
    items = []

    # 模式1: 苏州datalist-item
    for li in soup.select("li.datalist-item"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        title = link.get_text(" ", strip=True)
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        date_span = li.select_one("span.date")
        date_text = date_span.get_text(strip=True) if date_span else ""
        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式1b: 苏州ewb-list-node
    for li in soup.select("li.ewb-list-node"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        title = link.get_text(" ", strip=True)
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        date_span = li.select_one("span.ewb-list-date, span.date")
        date_text = date_span.get_text(strip=True) if date_span else ""
        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式2: 南通 initData ul.list-ul li
    for ul in soup.select("#initData ul.list-ul"):
        for li in ul.select("li"):
            link = li.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            date_span = li.select_one("span")
            date_text = date_span.get_text(strip=True) if date_span else ""
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式3: 通用ul li (支持多种结构)
    for li in soup.select("ul li"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        href = (link.get("href") or "").strip()
        if not href or "javascript" in href or href == "#":
            continue
        title = link.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue

        date_text = ""
        for span in li.select("span"):
            text = span.get_text(strip=True)
            if re.search(r"\\d{4}[-/.]\\d{1,2}[-/.]\\d{1,2}", text):
                date_text = text
                break
        if not date_text:
            full_text = li.get_text()
            date_match = re.search(r"(\\d{4}[-/.]\\d{1,2}[-/.]\\d{1,2})", full_text)
            if date_match:
                date_text = date_match.group(1)

        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})

    return items


def _get_page_count(soup):
    page_text = soup.get_text()
    total_match = re.search(r"总页数[：:]\\s*(\\d+)", page_text)
    if total_match:
        return int(total_match.group(1))
    total_match = re.search(r"totalRecord>(\\d+)", page_text)
    if total_match:
        return (int(total_match.group(1)) + 9) // 10
    return 1


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.proxies = {"http": None, "https": None}

    all_items = []
    page = 1
    while page <= 10:
        page_url = TARGET_URL if page == 1 else _build_page_url(TARGET_URL, page)
        try:
            response = session.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.content, "html.parser")
            items = _parse_items(soup)
            if not items:
                break
            for item in items:
                item["url"] = urljoin(page_url, item["url"])
            all_items.extend(items)
            total_pages = _get_page_count(soup)
            if page >= total_pages:
                break
            page += 1
        except Exception as exc:
            metrics.errors.append(f"列表页{page}抓取失败: {exc}")
            break

    metrics.raw_item_count = len(all_items)

    for item in all_items:
        metrics.valid_item_count += 1
        latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

        if not is_target_date(item["pub_at"], target_from, target_to):
            metrics.filtered_count += 1
            continue

        content = _extract_content(session, item["url"], metrics)
        policies.append({
            "title": item["title"],
            "url": item["url"],
            "pub_at": item["pub_at"],
            "content": content,
            "selected": False,
            "category": CATEGORY,
            "source": SOURCE_NAME,
        })

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


def _build_page_url(base_url, page):
    if page <= 1:
        return base_url
    if "?" in base_url:
        return base_url + f"&page={page}"
    if base_url.endswith(".shtml"):
        return base_url.replace(".shtml", f"_{page}.shtml")
    if base_url.endswith(".html"):
        return base_url.replace(".html", f"_{page}.html")
    return base_url


def run():
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )


if __name__ == "__main__":
    run()