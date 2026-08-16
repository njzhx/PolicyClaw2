"""
盐城市自然资源和规划局_规范性文件爬虫
目标栏目：https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=8a90825a590ac18e01590fabcee60025
页面机制：江苏省自然资源厅 gtapp 系统，服务端渲染列表，
          翻页通过 POST nrglIndex.action?classID=...&type=1（body 中 cpage=N）。
记录格式：<td class="nlist"><a title="..." href="...messageID=...">标题</a> <span>YYYY-MM-DD</span></td>
详情页正文：td[height="500"] 内 style 含 line-height:28px 的 td
"""
import re
import time
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


TARGET_URL = (
    "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
    "?classID=8a90825a590ac18e01590fabcee60025"
)
SOURCE_NAME = "盐城市自然资源和规划局_规范性文件"
CATEGORY = "盐城"
BASE_URL = "https://zrzy.jiangsu.gov.cn"
LIST_POST_URL = (
    "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
    "?classID=8a90825a590ac18e01590fabcee60025&type=1"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 30
DETAIL_SLEEP = 0.5


def _new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _parse_list_page(html, metrics):
    """解析 gtapp 列表页，返回 [{title, url, pub_at}]（按页面顺序）。"""
    soup = BeautifulSoup(html, "html.parser")
    cells = soup.select("td.nlist")
    metrics.raw_item_count += len(cells)
    items = []
    for cell in cells:
        try:
            link = cell.select_one("a[href]")
            date_elem = cell.select_one("span")
            if not link or not date_elem:
                metrics.invalid_item_count += 1
                continue
            title = (link.get("title") or link.get_text(" ", strip=True)).strip()
            href = (link.get("href") or "").strip()
            pub_at = parse_date(date_elem.get_text(strip=True))
            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue
            metrics.valid_item_count += 1
            items.append(
                {
                    "title": title,
                    "url": urljoin(BASE_URL, href),
                    "pub_at": pub_at,
                }
            )
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")
    return items


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = session.get(article_url, timeout=DETAIL_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(
            response.content.decode("utf-8", errors="replace"), "html.parser"
        )
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        # 正文在 height=500 的 td 内、style 含 line-height:28px 的 td 中
        content_elem = soup.select_one('td[style*="line-height:28px"]')
        if not content_elem:
            wrapper = soup.select_one('td[height="500"]')
            if wrapper:
                inner_tables = wrapper.select("table")
                if inner_tables:
                    content_elem = inner_tables[-1].select_one("td")
        if not content_elem:
            return ""
        return content_elem.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = _new_session()

    seen_urls = set()

    for page in range(1, MAX_PAGES + 1):
        try:
            if page == 1:
                response = session.get(TARGET_URL, timeout=LIST_TIMEOUT)
            else:
                response = session.post(
                    LIST_POST_URL, data={"cpage": str(page)}, timeout=LIST_TIMEOUT
                )
            response.raise_for_status()
            html = response.content.decode("utf-8", errors="replace")
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: 第{page}页 - {exc}")
            break

        items = _parse_list_page(html, metrics)
        if not items:
            if page == 1:
                metrics.errors.append("列表页解析失败或无数据")
            break

        new_items = []
        for item in items:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            new_items.append(item)

        for item in new_items:
            latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue

            content = _extract_content(session, item["url"], metrics)
            time.sleep(DETAIL_SLEEP)
            policies.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "pub_at": item["pub_at"],
                    "content": content,
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                }
            )

        # 最旧记录已早于目标窗口，停止翻页
        if items[-1]["pub_at"] < target_from:
            break
        time.sleep(0.5)

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
