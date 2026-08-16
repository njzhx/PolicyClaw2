# -*- coding: utf-8 -*-
"""淮安市自然资源和规划局_政策法规 栏目爬虫。

列表托管在江苏省自然资源厅 gtapp 平台（服务端渲染，td.nlist 结构），
第 1 页 GET 获取，后续页通过 POST 表单参数 cpage 翻页；
详情页为信息公开模板，正文在 td[style*="line-height:28px"]。
"""

import math
import re
from urllib.parse import urljoin

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy

try:
    from City.huaian_common import (
        LIST_TIMEOUT,
        MAX_PAGES,
        extract_main_content,
        fetch_soup,
        new_session,
    )
except ImportError:  # pragma: no cover - 兼容直接运行
    from huaian_common import (
        LIST_TIMEOUT,
        MAX_PAGES,
        extract_main_content,
        fetch_soup,
        new_session,
    )


TARGET_URL = (
    "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
    "?classID=2c9082b55a69433e015a69a5e34d004e"
)
LIST_ACTION = TARGET_URL + "&type=1"
SOURCE_NAME = "淮安市自然资源和规划局_政策法规"
CATEGORY = "淮安"

PAGE_INFO_RE = re.compile(r"共\s*(\d+)\s*条记录\s*每页\s*(\d+)\s*条")


def _fetch_page(session, page_index, metrics):
    """获取列表页；第 1 页 GET，后续页 POST cpage 表单翻页。"""
    if page_index == 1:
        return fetch_soup(session, TARGET_URL)
    response = session.post(
        LIST_ACTION, data={"cpage": str(page_index)}, timeout=LIST_TIMEOUT
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    from bs4 import BeautifulSoup

    return BeautifulSoup(response.text, "html.parser"), response.text


def _parse_total_pages(html):
    match = PAGE_INFO_RE.search(html)
    if not match:
        return 1
    total, size = int(match.group(1)), int(match.group(2))
    if size <= 0:
        return 1
    return max(1, math.ceil(total / size))


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    policies = []
    latest_candidates = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    total_pages = 1
    page_index = 1
    seen_urls = set()
    while page_index <= total_pages and page_index <= MAX_PAGES:
        try:
            soup, html = _fetch_page(session, page_index, metrics)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: page={page_index} - {exc}")
            break

        if page_index == 1:
            total_pages = _parse_total_pages(html)

        cells = soup.select("td.nlist")
        metrics.raw_item_count += len(cells)
        if not cells:
            metrics.errors.append(f"列表页未解析到条目: page={page_index}")

        page_items = []
        duplicates_before = metrics.duplicate_policy_count
        for cell in cells:
            link = cell.select_one("a[href]")
            if not link:
                metrics.invalid_item_count += 1
                continue
            title = (link.get("title") or "").strip() or link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            date_node = cell.select_one("span")
            pub_at = parse_date(date_node.get_text(strip=True)) if date_node else None
            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue
            article_url = urljoin(TARGET_URL, href)
            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)
            metrics.valid_item_count += 1
            page_items.append(
                {
                    "title": title,
                    "url": article_url,
                    "pub_at": pub_at,
                }
            )

        if cells and not page_items and metrics.duplicate_policy_count > duplicates_before:
            metrics.errors.append(
                f"列表页重复，已停止翻页: page={page_index}"
            )

        for item in page_items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "pub_at": item["pub_at"],
                    "content": extract_main_content(session, item["url"], metrics),
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                }
            )

        if not page_items:
            break
        newest_on_page = max(item["pub_at"] for item in page_items)
        if newest_on_page < target_from:
            break
        page_index += 1

    latest_sorted = sorted(latest_candidates, key=lambda x: x["pub_at"], reverse=True)
    latest_items = [
        {"title": item["title"], "pub_at": item["pub_at"]} for item in latest_sorted[:5]
    ]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items, metrics


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
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
