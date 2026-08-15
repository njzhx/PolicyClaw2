"""南通市人民政府_政府办文件 爬虫"""

import json
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


TARGET_URL = "https://www.nantong.gov.cn/ntsrmzf/szfbwj/szfbwj.html"
SOURCE_NAME = "南通市人民政府_政府办文件"
CATEGORY = "南通"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
# truecms jpage：perPage=10、groupSize=3，每组 30 条
GROUP_RECORDS = 30
MAX_GROUPS = 80

CONTENT_SELECTORS = (
    "#zoom",
    ".TRS_UEDITOR",
    ".TRS_EDITOR",
    "#zoomcon",
    ".pages_content",
    ".article",
    ".content",
    ".view",
)


def _extract_column_id(html):
    match = re.search(r"columnId:'([^']+)'", html)
    return match.group(1) if match else ""


def _extract_total_record(html):
    match = re.search(r"<totalrecord>(\d+)</totalrecord>", html)
    return int(match.group(1)) if match else 0


def _parse_li(li, base_url):
    """解析单个 li 节点，返回 (title, article_url, pub_at) 或 None。"""
    link = li.select_one("a")
    if not link:
        return None
    href = (link.get("href") or "").strip()
    title = (link.get("title") or "").strip()
    if not title:
        span_text = link.select_one("span.list-text")
        if span_text and span_text.get("title"):
            title = span_text.get("title").strip()
    if not title:
        title = link.get_text(" ", strip=True)
    if not href or not title:
        return None

    pub_at = None
    for span in li.select("span"):
        pub_at = parse_date(span.get_text(strip=True))
        if pub_at:
            break
    if not pub_at:
        return None
    return title, urljoin(base_url, href), pub_at


def _fetch_group(session, column_id, start_record, metrics):
    """调用 getMessage.do 接口抓取一组（30 条）记录，返回 li 节点列表。"""
    api_url = urljoin(TARGET_URL, "/truecms/messageController/getMessage.do")
    end_record = start_record + GROUP_RECORDS - 1
    try:
        response = session.get(
            api_url,
            params={
                "startrecord": start_record,
                "endrecord": end_record,
                "perpage": 10,
                "contentTemplate": "",
                "columnId": column_id,
                "callback": "jQuery1124",
            },
            headers={"Referer": TARGET_URL},
            timeout=LIST_TIMEOUT,
        )
        response.raise_for_status()
        text = response.content.decode("utf-8", errors="replace").strip()
        match = re.match(r"^[\w$;]*\((.*)\)\s*;?\s*$", text, re.DOTALL)
        if not match:
            metrics.errors.append(f"列表接口返回格式异常: startrecord={start_record}")
            return None
        payload = json.loads(match.group(1))
        records = re.findall(
            r"<record><!\[CDATA\[(.*?)\]\]></record>",
            payload.get("result", ""),
            re.DOTALL,
        )
        lis = []
        for record in records:
            soup = BeautifulSoup(record, "html.parser")
            li = soup.find("li")
            if li:
                lis.append(li)
        return lis
    except Exception as exc:
        metrics.errors.append(f"列表接口抓取失败: startrecord={start_record} - {exc}")
        return None


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(
            article_url,
            headers={"Referer": TARGET_URL},
            timeout=DETAIL_TIMEOUT,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        for selector in CONTENT_SELECTORS:
            element = soup.select_one(selector)
            if element:
                return element.get_text("\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        response = session.get(TARGET_URL, timeout=LIST_TIMEOUT)
        response.raise_for_status()
        html = response.content.decode("utf-8", errors="replace")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")
        return policies, latest_items, metrics

    column_id = _extract_column_id(html)
    total_record = _extract_total_record(html)
    soup = BeautifulSoup(html, "html.parser")
    init_data = soup.select_one("#initData")
    lis = init_data.select("ul.list-ul > li") if init_data else []

    start_record = 1
    groups_fetched = 0

    while True:
        metrics.raw_item_count += len(lis)
        oldest_on_page = None

        for li in lis:
            try:
                parsed = _parse_li(li, TARGET_URL)
                if not parsed:
                    metrics.invalid_item_count += 1
                    continue
                title, article_url, pub_at = parsed

                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})
                if oldest_on_page is None or pub_at < oldest_on_page:
                    oldest_on_page = pub_at

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

        groups_fetched += 1
        if not lis:
            break
        if oldest_on_page is None or oldest_on_page < target_from:
            break
        if groups_fetched >= MAX_GROUPS:
            break
        if not column_id:
            break
        start_record += GROUP_RECORDS
        if total_record and start_record > total_record:
            break
        lis = _fetch_group(session, column_id, start_record, metrics)
        if lis is None:
            break

    latest_items.sort(key=lambda item: item["pub_at"], reverse=True)
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
