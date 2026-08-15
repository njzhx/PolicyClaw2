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


TARGET_URL = "https://wglj.suzhou.gov.cn/szwhgdhlyj/zcjd/list.shtml"
SOURCE_NAME = "苏州市文化广电和旅游局_政策解读"
CATEGORY = "苏州"
MAX_PAGES = 100

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
        element = soup.select_one("div.text")
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
    seen_urls = set()
    seen_page_signatures = set()

    for page in range(1, MAX_PAGES + 1):
        if page == 1:
            list_url = TARGET_URL
        else:
            list_url = TARGET_URL.replace("list.shtml", f"list_{page}.shtml")

        try:
            response = session.get(list_url, headers=HEADERS, timeout=30)
            if page > 1 and response.status_code == 404:
                break
            response.raise_for_status()
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败(page={page}): {exc}")
            break

        soup = BeautifulSoup(response.content, "html.parser")
        nodes = soup.select("div.list")
        if not nodes:
            break

        page_signature = tuple(
            urljoin(list_url, (link.get("href") or "").strip())
            for link in (node.select_one("h2 > a") for node in nodes)
            if link and (link.get("href") or "").strip()
        )
        if page_signature in seen_page_signatures:
            break
        seen_page_signatures.add(page_signature)

        metrics.raw_item_count += len(nodes)
        oldest_date_on_page = None

        for node in nodes:
            try:
                link = node.select_one("h2 > a")
                if not link:
                    metrics.invalid_item_count += 1
                    continue
                title = (link.get("title") or link.get_text(" ", strip=True)).strip()
                href = (link.get("href") or "").strip()
                date_span = node.select_one("h2 > span")
                raw_date = date_span.get_text(strip=True) if date_span else ""
                pub_at = parse_date(raw_date)

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(list_url, href)
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

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

        if oldest_date_on_page and oldest_date_on_page < target_from:
            break

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
