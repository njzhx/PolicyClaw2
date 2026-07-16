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

TARGET_URL = "https://gxj.nanjing.gov.cn/njsjjhxxhwyh/214/index_17380.html"
SOURCE_NAME = "南京市工业和信息化局_法定主动公开内容"
CATEGORY = "南京"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

PAGE_SIZE = 20


def _extract_content(session, article_url, metrics):
    try:
        resp = session.get(article_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        element = soup.select_one("div.view.TRS_UEDITOR")
        if element:
            return element.get_text("\n", strip=True)
        con = soup.select_one("div.con")
        return con.get_text("\n", strip=True) if con else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_17380_{page_index}.html"

            resp = session.get(page_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")
            nodes = soup.select("#result > li")

            if not nodes:
                break

            page_raw_count = len(nodes)
            metrics.raw_item_count += page_raw_count

            oldest_date_on_page = None
            for node in nodes:
                try:
                    link = node.select_one("span.d1 a")
                    title = link.get_text(" ", strip=True) if link else ""
                    href = (link.get("href") or "").strip() if link else ""
                    date_span = node.select_one("span.d2")
                    pub_at = (
                        parse_date(date_span.get_text(strip=True))
                        if date_span
                        else None
                    )

                    if not title or not href or not pub_at:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin(TARGET_URL, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    policies.append(
                        {
                            "title": title,
                            "url": article_url,
                            "pub_at": pub_at,
                            "content": _extract_content(
                                session, article_url, metrics
                            ),
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
            if page_raw_count < PAGE_SIZE:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

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
