# -*- coding: utf-8 -*-
"""镇江市自然资源和规划局_政策法规 爬虫。

数据在江苏省自然资源厅统一平台（zrzy.jiangsu.gov.cn）的镇江频道，
栏目为"政策法规"。列表页为服务端渲染，条目结构为
``td.nlist > a[title] + span``（YYYY-MM-DD）；分页通过 POST 表单
提交 ``cpage=N`` 实现，每页 25 条；详情页为
``nrglIndex.action?type=2&messageID=...``，正文在正中央 td 内。
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy
try:
    from City.zhenjiang_common import (
        DETAIL_TIMEOUT,
        LIST_TIMEOUT,
        new_session,
    )
except ImportError:  # pragma: no cover - 兼容直接运行
    from zhenjiang_common import DETAIL_TIMEOUT, LIST_TIMEOUT, new_session


TARGET_URL = (
    "http://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
    "?classID=2c9082b55b6b7170015b6b8880f00029"
)
SOURCE_NAME = "镇江市自然资源和规划局_政策法规"
CATEGORY = "镇江"

LIST_URL = TARGET_URL + "&type=1"
MAX_PAGES = 40
TOTAL_PAGE_RE = re.compile(r"当前\(\s*\d+\s*/\s*(\d+)\s*\)页")


def _fetch_text(session, url, method="get", data=None, timeout=LIST_TIMEOUT):
    if method == "post":
        response = session.post(url, data=data, timeout=timeout)
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def _extract_content(session, article_url, metrics):
    try:
        text = _fetch_text(session, article_url, timeout=DETAIL_TIMEOUT)
        soup = BeautifulSoup(text, "html.parser")
        element = soup.select_one('td[style*="line-height:28px"]')
        if not element:
            # 退化策略：取文本最长的 td
            candidates = soup.find_all("td")
            element = max(
                candidates, key=lambda td: len(td.get_text(strip=True)), default=None
            )
        if not element:
            return ""
        for tag in element.find_all(["script", "style"]):
            tag.decompose()
        return element.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_list_page(soup):
    records = []
    oldest_date = None
    for node in soup.select("td.nlist"):
        link = node.find("a")
        if not link:
            continue
        title = (link.get("title") or link.get_text(" ", strip=True) or "").strip()
        href = (link.get("href") or "").strip()
        date_node = node.find("span")
        pub_at = parse_date(date_node.get_text(strip=True)) if date_node else None
        if not title or not href or not pub_at:
            continue
        records.append(
            {
                "title": title,
                "url": urljoin(LIST_URL, href.replace("&amp;", "&")),
                "pub_at": pub_at,
            }
        )
        if oldest_date is None or pub_at < oldest_date:
            oldest_date = pub_at
    return records, oldest_date


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()
    seen_urls = set()

    try:
        first_html = _fetch_text(session, LIST_URL, timeout=LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {LIST_URL} - {exc}")
        return policies, latest_items, metrics

    total_match = TOTAL_PAGE_RE.search(first_html)
    total_pages = min(int(total_match.group(1)), MAX_PAGES) if total_match else 1

    page_index = 1
    consecutive_empty_pages = 0
    while page_index <= total_pages:
        try:
            if page_index == 1:
                html = first_html
            else:
                html = _fetch_text(
                    session,
                    LIST_URL,
                    method="post",
                    data={"cpage": str(page_index)},
                    timeout=LIST_TIMEOUT,
                )
        except Exception as exc:
            metrics.errors.append(f"列表分页抓取失败 [第{page_index}页]: {exc}")
            break

        soup = BeautifulSoup(html, "html.parser")
        records, oldest_date = _parse_list_page(soup)
        metrics.raw_item_count += len(records)

        # 空页止损：首页无记录说明栏目页结构失效，无需翻页；
        # 后续页连续空页同理，避免失控翻满 MAX_PAGES。
        if not records:
            consecutive_empty_pages += 1
            if page_index == 1:
                metrics.errors.append(f"列表页未解析到记录，停止翻页: {LIST_URL}")
                break
            if consecutive_empty_pages >= 2:
                metrics.errors.append(
                    f"连续 {consecutive_empty_pages} 页未解析到记录，停止翻页 "
                    f"[第{page_index}页]"
                )
                break
        else:
            consecutive_empty_pages = 0

        for record in records:
            if record["url"] in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(record["url"])
            metrics.valid_item_count += 1
            if page_index == 1:
                latest_items.append(
                    {"title": record["title"], "pub_at": record["pub_at"]}
                )
            if not is_target_date(record["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                {
                    "title": record["title"],
                    "url": record["url"],
                    "pub_at": record["pub_at"],
                    "content": _extract_content(session, record["url"], metrics),
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                }
            )

        if oldest_date and oldest_date < target_from:
            break
        page_index += 1

    latest_items = sorted(latest_items, key=lambda x: x["pub_at"], reverse=True)[:5]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
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
