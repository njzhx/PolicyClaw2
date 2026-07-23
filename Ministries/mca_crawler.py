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


TARGET_URL = "https://www.mca.gov.cn/gdnps/searchIndex.jsp?params=%257B%2522goPage%2522%253A1%252C%2522orderBy%2522%253A%255B%257B%2522orderBy%2522%253A%2522scrq%2522%252C%2522reverse%2522%253Atrue%257D%252C%257B%2522orderBy%2522%253A%2522orderTime%2522%252C%2522reverse%2522%253Atrue%257D%255D%252C%2522pageSize%2522%253A15%252C%2522queryParam%2522%253A%255B%257B%2522shortName%2522%253A%2522ownSubjectDn%2522%252C%2522value%2522%253A%2522%252F1%252F139%252F2445%252F2575%2522%257D%252C%257B%2522shortName%2522%253A%2522fbjg%2522%252C%2522value%2522%253A%2522%252F1%252F139%252F2445%252F2575%2522%257D%252C%257B%257D%252C%257B%257D%255D%252C%2522doRepeated%2522%253A0%257D"
SOURCE_NAME = "民政部政策文件"
CATEGORY = "中央部委"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.mca.gov.cn/gdnps/pc/index.jsp?mtype=1",
}


def _parse_json_payload(text):
    payload = text.strip()
    if payload.startswith("("):
        payload = payload[1:]
    if payload.endswith(")"):
        payload = payload[:-1]

    json_start = payload.find("{")
    json_end = payload.rfind("}")
    if json_start != -1 and json_end > json_start:
        payload = payload[json_start : json_end + 1]
    return json.loads(payload)


def _parse_publish_date(record):
    for key in ("publishTime", "scrq", "orderTime"):
        value = record.get(key)
        if not value:
            continue
        text = str(value).strip()
        if len(text) >= 8 and text[:8].isdigit():
            parsed = parse_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
        else:
            parsed = parse_date(text)
        if parsed:
            return parsed
    return None


def _extract_content(record):
    html_content = record.get("htmlContent") or record.get("content") or ""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text("\n", strip=True)


def _record_url(record):
    href = str(record.get("url") or "").strip()
    if href:
        return urljoin("https://www.mca.gov.cn/", href)
    record_id = str(record.get("id") or "").strip()
    if record_id:
        return f"https://www.mca.gov.cn/gdnps/pc/content.jsp?id={record_id}"
    return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()

    try:
        response = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = _parse_json_payload(response.text)
        records = data.get("resultMap") or []
        metrics.raw_item_count = len(records)

        if not records:
            metrics.errors.append("API 未返回 resultMap 数据")

        for record in records:
            try:
                title = str(record.get("title") or "").strip()
                article_url = _record_url(record)
                pub_at = _parse_publish_date(record)

                if not title or not article_url or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(
                        f"API记录核心字段缺失: {title or article_url or '未知条目'}"
                    )
                    continue

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": _extract_content(record),
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"API记录解析失败: {exc}")
    except Exception as exc:
        metrics.errors.append(f"API抓取失败: {exc}")

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
