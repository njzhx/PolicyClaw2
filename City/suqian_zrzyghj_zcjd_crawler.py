"""
宿迁市自然资源和规划局_政策解读爬虫
目标页面：https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=ff8080817d47089b017d6ac4dc050adf
列表结构：a[href*="nrglIndex.action?type=2&messageID="] 父级包含标题和日期
正文容器：.article-content
分页：type=1&page=N
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


TARGET_URL = "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=ff8080817d47089b017d6ac4dc050adf"
SOURCE_NAME = "宿迁市自然资源和规划局_政策解读"
CATEGORY = "宿迁"
BASE_URL = "https://zrzy.jiangsu.gov.cn"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}
PROXIES = {"http": None, "https": None}


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15, proxies=PROXIES)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        article = soup.select_one(".article-content")
        if article:
            for tag in article.find_all(["script", "style"]):
                tag.decompose()
            content = article.get_text("\n", strip=True)
        else:
            content = ""
        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取宿迁市自然资源和规划局政策解读"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 1
    max_pages = 100
    all_items = []

    while page_index <= max_pages:
        page_url = f"{TARGET_URL}&type=1&page={page_index}"

        try:
            response = session.get(page_url, headers=HEADERS, timeout=30, proxies=PROXIES)
            if response.status_code == 404:
                break
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.content, "html.parser")

            # 政策解读列表在 a[href*="type=2&messageID="] 中
            article_links = soup.select('a[href*="type=2&messageID="]')

            if not article_links:
                break

            # 对每页去重（可能有重复）
            seen_hrefs = set()
            for a in article_links:
                href = a.get("href", "").strip()
                if href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    all_items.append(a)

            # 检查最旧日期
            page_dates = []
            for a in article_links:
                parent = a.parent
                if parent:
                    text = parent.get_text(" ", strip=True)
                    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
                    if match:
                        d = parse_date(match.group(1))
                        if d:
                            page_dates.append(d)

            if page_dates:
                oldest = min(page_dates)
                if oldest < target_from:
                    break

            if len(seen_hrefs) < 3:
                break

        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        page_index += 1

    metrics.raw_item_count = len(all_items)

    for a in all_items:
        try:
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#") or "javascript" in href.lower():
                metrics.invalid_item_count += 1
                continue

            parent = a.parent
            if not parent:
                metrics.invalid_item_count += 1
                continue

            title = a.get_text(" ", strip=True)
            if not title:
                metrics.invalid_item_count += 1
                continue

            # 从父级文本提取日期
            parent_text = parent.get_text(" ", strip=True)
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", parent_text)
            pub_at = parse_date(date_match.group(1)) if date_match else None

            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"无法解析日期: {title[:30]}...")
                continue

            article_url = urljoin(BASE_URL, href)
            metrics.valid_item_count += 1
            latest_items.append({"title": title, "pub_at": pub_at})

            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue

            content = _extract_content(session, article_url, metrics)

            policies.append({
                "title": title,
                "url": article_url,
                "pub_at": pub_at,
                "content": content,
                "selected": False,
                "category": CATEGORY,
                "source": SOURCE_NAME,
            })
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


def run():
    """执行爬虫"""
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
