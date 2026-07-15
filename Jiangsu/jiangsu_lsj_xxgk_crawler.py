import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    external_send_enabled,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://lsj.jiangsu.gov.cn/col/col81951/index.html"
SOURCE_NAME = "江苏省粮食和物资储备局_信息公开"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _extract_records(soup):
    for script in soup.find_all("script"):
        if script.string and "<record>" in script.string:
            return BeautifulSoup(script.string, "html.parser").find_all("record")
    return []


def _extract_content(article_url, title, metrics):
    try:
        response = requests.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        detail_soup = BeautifulSoup(response.content, "html.parser")
        selectors = (
            ".TRS_Editor",
            ".zoom",
            "#zoom",
            ".content",
            "#content",
            ".article-content",
            ".main-txt",
            'div[class*="content"]',
        )
        for selector in selectors:
            element = detail_soup.select_one(selector)
            if not element:
                continue
            lines = [line.strip() for line in element.get_text("\n", strip=True).splitlines() if line.strip()]
            if lines:
                content = "\n".join(lines)
                if len(content) < 50:
                    metrics.errors.append(f"正文内容较短: {title} ({len(content)} 字符)")
                return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
    return ""


def scrape_data():
    policies = []
    all_items = []
    metrics = CrawlerMetrics()
    target_date_from, target_date_to = get_crawl_date_window()

    try:
        response = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        records = _extract_records(BeautifulSoup(response.content, "html.parser"))
        metrics.raw_item_count = len(records)
        if not records:
            metrics.errors.append("列表页未找到 record 数据记录")
            return policies, all_items, metrics

        for record in records:
            try:
                cdata_content = record.string
                if not cdata_content:
                    metrics.invalid_item_count += 1
                    continue

                list_soup = BeautifulSoup(cdata_content, "html.parser")
                link = list_soup.find("a")
                if not link:
                    metrics.invalid_item_count += 1
                    continue

                title = (link.get("title") or link.get_text(strip=True) or "").strip()
                href = (link.get("href") or "").strip()
                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(TARGET_URL, href)
                pub_at = None
                span = list_soup.find("span")
                if span:
                    pub_at = parse_date(span.get_text(strip=True))
                if not pub_at:
                    path_date = re.search(r"/(\d{4})/(\d{1,2})/(\d{1,2})/", href)
                    if path_date:
                        pub_at = parse_date("-".join(path_date.groups()))

                all_items.append({"title": title, "pub_at": pub_at})
                if not is_target_date(pub_at, target_date_from, target_date_to):
                    metrics.filtered_count += 1
                    continue

                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": _extract_content(article_url, title, metrics),
                        "selected": False,
                        "category": '江苏省本级',
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {exc}")

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.valid_item_count = len(all_items)
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, all_items, metrics


def run():
    data, latest_items, metrics = scrape_data()
    saved_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    metrics.saved_count = len(saved_items)

    if not data:
        storage_result = {
            "status": "skipped",
            "saved_count": 0,
            "message": "没有数据需要写入",
        }
    elif not external_send_enabled():
        storage_result = {
            "status": "dry_run",
            "saved_count": len(saved_items),
            "message": f"DRY-RUN：模拟写入 {len(saved_items)} 条数据到 Supabase",
        }
    elif saved_items:
        storage_result = {
            "status": "success",
            "saved_count": len(saved_items),
            "message": f"成功写入 {len(saved_items)} 条数据到 Supabase",
        }
    else:
        storage_result = {
            "status": "error",
            "saved_count": 0,
            "message": "数据库写入失败",
        }

    return CrawlerRunResult(
        items=saved_items,
        latest_items=latest_items[:5],
        metrics=metrics,
        storage_result=storage_result,
        api_push_result=api_push_result,
    )


if __name__ == "__main__":
    run()
