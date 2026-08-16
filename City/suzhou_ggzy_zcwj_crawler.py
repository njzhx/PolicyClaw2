import json
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


TARGET_URL = "https://ggzy.suzhou.gov.cn/zcwj/moreinfo.html"
API_URL = (
    "https://ggzy.suzhou.gov.cn/EpointWebBuilder/"
    "XyxxSearchAction.action?cmd=getList"
)
DETAIL_PATH_API_URL = (
    "https://ggzy.suzhou.gov.cn/EpointWebBuilder/"
    "JyxxSearchAction.action?cmd=getDetailPath"
)
SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
SOURCE_NAME = "苏州市公共资源交易中心_政策文件"
CATEGORY = "苏州"
PAGE_SIZE = 15
MAX_PAGES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


def _get_with_retry(session, url, **kwargs):
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 2:
                raise
    raise last_error


def _extract_content(session, article_url, metrics):
    try:
        response = _get_with_retry(
            session, article_url, headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(response.content, "html.parser")
        element = (
            soup.select_one(".ewb-zoom")
            or soup.select_one("#zoomcon")
            or soup.select_one("div.con")
        )
        return element.get_text("\n", strip=True) if element else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _resolve_article_url(session, record, fallback_url, metrics):
    try:
        response = _get_with_retry(
            session,
            DETAIL_PATH_API_URL,
            params={
                "categorynum": record.get("categorynum") or "040",
                "infoid": record.get("infoid") or "",
                "siteguid": SITE_GUID,
                "pageIndex": 0,
            },
            headers={**HEADERS, "Referer": fallback_url},
            timeout=15,
        )
        detail_path = str(response.json().get("custom") or "").strip()
        return urljoin(TARGET_URL, detail_path) if detail_path else fallback_url
    except Exception as exc:
        metrics.errors.append(f"详情地址解析失败: {fallback_url} - {exc}")
        return fallback_url


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    seen_urls = set()
    seen_page_signatures = set()

    for page in range(1, MAX_PAGES + 1):
        try:
            response = _get_with_retry(
                session,
                API_URL,
                params={
                    "categoryNum": "040",
                    "title": "",
                    "starttime": "",
                    "endtime": "",
                    "pageIndex": page,
                    "pageSize": PAGE_SIZE,
                },
                headers={**HEADERS, "Referer": TARGET_URL},
                timeout=30,
            )
            payload = response.json()
            page_data = json.loads(payload.get("custom") or "{}")
        except Exception as exc:
            metrics.errors.append(f"列表 API 抓取失败(page={page}): {exc}")
            break

        records = page_data.get("Table") or []
        if not records:
            break

        page_signature = tuple(
            str(record.get("infoid") or record.get("infourl") or "")
            for record in records
        )
        if page_signature in seen_page_signatures:
            break
        seen_page_signatures.add(page_signature)

        metrics.raw_item_count += len(records)
        oldest_date_on_page = None

        for record in records:
            try:
                title = BeautifulSoup(
                    str(record.get("title") or record.get("title1") or ""),
                    "html.parser",
                ).get_text(" ", strip=True)
                href = str(record.get("infourl") or "").strip()
                raw_date = str(record.get("date") or "").strip()
                pub_at = parse_date(raw_date)

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                fallback_url = urljoin(TARGET_URL, href)
                record_key = str(record.get("infoid") or fallback_url)
                if record_key in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(record_key)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                article_url = _resolve_article_url(
                    session, record, fallback_url, metrics
                )
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

        total_count = int(page_data.get("TotalCount") or 0)
        if total_count and page * PAGE_SIZE >= total_count:
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
