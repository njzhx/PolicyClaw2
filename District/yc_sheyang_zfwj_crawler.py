"""
盐城市射阳县_政府发文爬虫
目标栏目：https://www.sheyang.gov.cn/col/col30675/index.html
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


TARGET_URL = "https://www.sheyang.gov.cn/col/col30675/index.html"
SOURCE_NAME = "盐城市射阳县_政府发文"
CATEGORY = "盐城_射阳县"

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
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one(".pages_content")
            or soup.select_one("#zoom")
            or soup.select_one(".xlxlcont")
            or soup.select_one(".Custom_UnionStyle")
            or soup.select_one(".article")
            or soup.select_one(".content")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\n", strip=True)
        return ""
        desc_meta = soup.select_one('meta[name="Description"]')
        if desc_meta and desc_meta.get("content"):
            return desc_meta["content"].strip()
        metrics.errors.append(f"正文选择器未命中: {article_url}")
        return ""

    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        for li in soup.select("li"):
            link = li.select_one("a")
            if not link or not link.get("href"):
                continue
            href = (link.get("href") or "").strip()
            if not href or "javascript" in href or href == "#":
                continue
            title = link.get("title") or link.get_text(" ", strip=True)
            title = title.strip()
            if not title or len(title) < 5:
                continue
            date_text = ""
            for span in li.select("span, b"):
                text = span.get_text(strip=True)
                if re.search(r"\d{4}-\d{2}-\d{2}", text):
                    date_text = text
                    break
            if not date_text:
                m = re.search(r"(\d{4}-\d{2}-\d{2})", li.get_text())
                if m:
                    date_text = m.group(1)
            pub_at = parse_date(date_text) if date_text else None
            if not pub_at:
                continue
            article_url = urljoin(TARGET_URL, href)
            metrics.valid_item_count += 1
            latest_items.append({"title": title, "pub_at": pub_at})
            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append({
                "title": title,
                "url": article_url,
                "pub_at": pub_at,
                "content": _extract_content(session, article_url, metrics),
                "selected": False,
                "category": CATEGORY,
                "source": SOURCE_NAME,
            })
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.raw_item_count = metrics.valid_item_count + metrics.invalid_item_count
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
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
