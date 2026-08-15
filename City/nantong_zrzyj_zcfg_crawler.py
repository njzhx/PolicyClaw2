"""南通市自然资源和规划局_政策法规 爬虫（江苏省自然资源厅 gtapp 信息公开系统）"""

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


TARGET_URL = "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=2c9082b55bd83c30015bdb914b5c0118"
SOURCE_NAME = "南通市自然资源和规划局_政策法规"
CATEGORY = "南通"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 40


def _list_page_url(page_index):
    """cpage 参数由 gtapp 后端读取（GET 方式实测有效）。"""
    sep = "&" if "?" in TARGET_URL else "?"
    return f"{TARGET_URL}{sep}type=1&cpage={page_index}"


def _parse_list_rows(soup):
    """解析列表页 td.nlist 行，返回 (title, href, pub_at) 列表。"""
    rows = []
    for td in soup.select("td.nlist"):
        link = td.select_one("a")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        title = (link.get("title") or "").strip() or link.get_text(" ", strip=True)
        if not href or not title:
            continue
        pub_at = None
        for span in td.select("span"):
            pub_at = parse_date(span.get_text(strip=True))
            if pub_at:
                break
        if not pub_at:
            continue
        rows.append((title, href, pub_at))
    return rows


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(
            article_url,
            headers={"Referer": TARGET_URL},
            timeout=DETAIL_TIMEOUT,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        # gtapp 详情页正文在 style 含 line-height:28px 的 td 中
        element = soup.select_one('td[style*="line-height:28px"]')
        if element:
            return element.get_text("\n", strip=True)
        for selector in ("#zoom", ".TRS_UEDITOR", ".TRS_EDITOR", ".content"):
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

    page_index = 1
    while page_index <= MAX_PAGES:
        page_url = _list_page_url(page_index)
        try:
            response = session.get(page_url, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            html = response.content.decode("utf-8", errors="replace")
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: 第{page_index}页 - {exc}")
            break

        soup = BeautifulSoup(html, "html.parser")
        rows = _parse_list_rows(soup)
        metrics.raw_item_count += len(rows)

        if not rows:
            break

        oldest_on_page = None
        for title, href, pub_at in rows:
            try:
                article_url = urljoin(page_url, href)
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

        if oldest_on_page is None or oldest_on_page < target_from:
            break
        page_index += 1

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
