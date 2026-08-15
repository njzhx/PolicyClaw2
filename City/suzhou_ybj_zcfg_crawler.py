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


TARGET_URL = "https://ybj.suzhou.gov.cn/szybj/zcfg/nav_list.shtml"
SOURCE_NAME = "苏州市医疗保障局_政策法规"
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
        element = soup.select_one("#zoomcon UCAPCONTENT")
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

    max_pages = 200

    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            page_url = TARGET_URL
        else:
            page_url = f"{TARGET_URL}?{page_num}"

        try:
            response = session.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 (页{page_num}): {exc}")
            break

        soup = BeautifulSoup(response.content, "html.parser")
        nodes = soup.select("ul.infolist > li")

        if not nodes:
            break

        metrics.raw_item_count += len(nodes)
        page_has_target = False

        for node in nodes:
            try:
                link = node.select_one("a.elli-s")
                if not link:
                    metrics.invalid_item_count += 1
                    continue
                title = link.get_text(" ", strip=True)
                href = (link.get("href") or "").strip()
                date_elem = node.select_one("span.time")
                raw_date = date_elem.get_text(strip=True) if date_elem else ""
                pub_at = parse_date(raw_date)

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(page_url, href)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if is_target_date(pub_at, target_from, target_to):
                    page_has_target = True
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
                else:
                    metrics.filtered_count += 1
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {exc}")

        oldest_on_page = None
        for node in nodes:
            try:
                date_elem = node.select_one("span.time")
                if date_elem:
                    oldest_on_page = parse_date(date_elem.get_text(strip=True))
            except Exception:
                pass
        if oldest_on_page and oldest_on_page < target_from:
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