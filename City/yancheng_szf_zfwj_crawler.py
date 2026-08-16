"""
盐城市人民政府_政府文件及解读爬虫
目标栏目：https://www.yancheng.gov.cn/col/col23755/index.html
页面机制：服务端渲染静态聚合页，含"市政府文件/市政府办公室文件/政策解读"三个区块，
          每区块展示最新 10 条，无分页。
记录格式：<li><a title="..." href="/art/...">标题</a><b>YYYY-MM-DD</b></li>
详情页正文：#zoom
"""
import re
import time
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


TARGET_URL = "https://www.yancheng.gov.cn/col/col23755/index.html"
SOURCE_NAME = "盐城市人民政府_政府文件及解读"
CATEGORY = "盐城"
BASE_URL = "https://www.yancheng.gov.cn"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
DETAIL_SLEEP = 0.5


def _new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _parse_list_page(html, metrics):
    """解析静态聚合列表：每个 li 内 a[title][href] + b 日期。"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls = set()
    for li in soup.select("li"):
        link = li.select_one("a[href*='/art/']")
        date_elem = li.select_one("b")
        if not link or not date_elem:
            continue
        pub_at = parse_date(date_elem.get_text(strip=True))
        if not pub_at:
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            metrics.invalid_item_count += 1
            continue
        url = urljoin(BASE_URL, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append({"title": title, "url": url, "pub_at": pub_at})
    metrics.raw_item_count = len(items)
    metrics.valid_item_count = len(items)
    return items


def _extract_content(session, article_url, metrics):
    """提取详情页正文；首个响应若为加速乐挑战页（过短且无正文容器）则重试一次。"""
    try:
        html = ""
        for _ in range(2):
            response = session.get(article_url, timeout=DETAIL_TIMEOUT)
            response.raise_for_status()
            html = response.content.decode("utf-8", errors="replace")
            if len(html) >= 2000 or "zoom" in html:
                break
            time.sleep(1)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        content_elem = (
            soup.select_one("#zoom")
            or soup.select_one(".zoom")
            or soup.select_one("#zoomcon")
            or soup.select_one("div.article")
            or soup.select_one("div.wp.article-content")
        )
        if not content_elem:
            return ""
        return content_elem.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = _new_session()

    try:
        response = session.get(TARGET_URL, timeout=LIST_TIMEOUT)
        response.raise_for_status()
        html = response.content.decode("utf-8", errors="replace")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics

    items = _parse_list_page(html, metrics)
    if not items:
        metrics.errors.append("列表页解析失败或无数据")
        return policies, latest_items, metrics

    for item in items:
        latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

        if not is_target_date(item["pub_at"], target_from, target_to):
            metrics.filtered_count += 1
            continue

        content = _extract_content(session, item["url"], metrics)
        time.sleep(DETAIL_SLEEP)
        policies.append(
            {
                "title": item["title"],
                "url": item["url"],
                "pub_at": item["pub_at"],
                "content": content,
                "selected": False,
                "category": CATEGORY,
                "source": SOURCE_NAME,
            }
        )

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


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
