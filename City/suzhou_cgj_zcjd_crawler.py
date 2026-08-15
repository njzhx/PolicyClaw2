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

TARGET_URL = "https://cgj.suzhou.gov.cn/srsz/zcjdlm/zcwj_list.shtml"
SOURCE_NAME = "苏州市城市管理局_政策解读"
CATEGORY = "苏州"

API_URL = "https://cgj.suzhou.gov.cn/szinf/interfacesWebManu/loadDataByChannelIdAndDeptIdBackSort"
CHANNEL_ID = "0cbe2285242f40f0b6a3e8dc6f76eb1d"
DEPT_CODE = "11320500014149041N"
PAGE_SIZE = 15
MAX_PAGES = 200

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one("#zoomcon UCAPCONTENT") or soup.select_one("#zoomcon")
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

    page_index = 1
    total_records = None
    oldest_date_on_page = None

    try:
        while page_index <= MAX_PAGES:
            try:
                resp = session.post(
                    API_URL,
                    data={
                        "channelId": CHANNEL_ID,
                        "deptCode": DEPT_CODE,
                        "pageSize": PAGE_SIZE,
                        "pageIndex": page_index,
                    },
                    headers=HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                metrics.errors.append(f"列表API请求失败 (page={page_index}): {exc}")
                break

            if str(data.get("code")) != "0":
                metrics.errors.append(f"API返回code非0: {data.get('msg')}")
                break

            list_data = data.get("data", {}) if isinstance(data, dict) else {}
            records = list_data.get("list", []) if isinstance(list_data, dict) else []
            if not records:
                break

            if page_index == 1:
                total_records = list_data.get("allRow")

            metrics.raw_item_count += len(records)

            for record in records:
                try:
                    title = (record.get("TITLE") or "").strip()
                    href = (record.get("URL_COMMP") or "").strip()
                    pub_date_str = (record.get("PUBLISHED_TIME_FORMAT") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin("https://www.suzhou.gov.cn/", href.lstrip("/")) if href else ""
                    pub_at = parse_date(pub_date_str)

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        continue

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
                            "content": _extract_content(session, article_url, metrics),
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
            if total_records and page_index * PAGE_SIZE >= total_records:
                break
            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

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
