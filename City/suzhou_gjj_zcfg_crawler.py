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


TARGET_URL = "https://gjj.suzhou.gov.cn/szgjj/zcfg/nav_xjzl_zc.shtml"
SOURCE_NAME = "苏州市住房公积金管理中心_政策法规"
CATEGORY = "苏州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one(".ewb-zoom") or soup.select_one("#zoomcon")
        return element.get_text("\n", strip=True) if element else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")
        return policies, latest_items, metrics

    soup = BeautifulSoup(response.content, "html.parser")
    nodes = soup.select("li.ewb-right-item")
    if not nodes:
        metrics.errors.append("列表页未找到 li.ewb-right-item 节点")
        return policies, latest_items, metrics

    metrics.raw_item_count = len(nodes)

    for node in nodes:
        try:
            link = node.select_one("a")
            if not link:
                metrics.invalid_item_count += 1
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            date_span = node.select_one("span.wb-data-date")
            raw_date = date_span.get_text(strip=True) if date_span else ""
            pub_at = parse_date(raw_date)

            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue

            article_url = urljoin(TARGET_URL, href)
            metrics.valid_item_count += 1
            latest_items.append({"title": title, "pub_at": pub_at})

            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue

            content = _extract_content(session, article_url, metrics)
            policies.append(
                {
                    "title": title,
                    "url": article_url,
                    "pub_at": pub_at,
                    "content": content,
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                }
            )
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")

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
