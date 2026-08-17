"""常州市工业和信息化局_政策解读 爬虫。

站点：常州市工信局 政策解读（子站 class 列表）
列表为服务端渲染表格，按发布日期倒序，支持路径分页。
"""

from urllib.parse import urljoin

import re

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


TARGET_URL = "https://gxj.changzhou.gov.cn/class/PQFJPFCK"
SOURCE_NAME = "常州市工业和信息化局_政策解读"
CATEGORY = "常州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

BASE_URL = "https://gxj.changzhou.gov.cn"
MAX_PAGES = 200
PAGE_SIZE = 20

DETAIL_SELECTORS = (
    "td#czfxcontent",
    "td#czfxfontzoom",
    "td.GovInfoContent",
    "td.NewsContent",
    "td.NewsText",
    "div#zoom",
    "div.pages_content",
)

LIST_CONTAINER_SELECTOR = "table.border2, td.listtext > table"
DATE_CELL_PATTERN = re.compile(r"^\[?\d{4}-\d{2}-\d{2}\]?$")


def _extract_content(session, article_url, metrics):
    """抓取详情页正文，依次尝试多个正文容器选择器。"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
        if not response.text.strip():
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for selector in DETAIL_SELECTORS:
            element = soup.select_one(selector)
            if not element:
                continue
            for node in element.select("script, style"):
                node.decompose()
            text = element.get_text("\n", strip=True)
            if text:
                return text
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _iter_list_rows(soup):
    """定位列表表格容器；容器缺失时回退到整篇文档。"""
    containers = soup.select(LIST_CONTAINER_SELECTOR)
    rows = []
    if containers:
        for container in containers:
            rows.extend(container.find_all("tr"))
    else:
        rows = soup.find_all("tr")
    return rows


def _parse_list_page(html):
    """解析子站 class 列表，返回 [{title, href, date}]。"""
    soup = BeautifulSoup(html, "html.parser")
    nodes = []
    seen_row_ids = set()
    for row in _iter_list_rows(soup):
        if id(row) in seen_row_ids:
            continue
        seen_row_ids.add(id(row))
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        raw_date = cells[-1].get_text(" ", strip=True)
        if not DATE_CELL_PATTERN.match(raw_date):
            continue
        link = None
        for cell in cells:
            link = cell.find("a", href=True, title=True)
            if link:
                break
        if not link:
            continue
        href = link.get("href", "").strip()
        if not href or href.startswith("javascript"):
            continue
        title = (link.get("title") or "").strip()
        if not title:
            title = link.get_text(" ", strip=True).lstrip("·").strip()
        nodes.append({"title": title, "href": href, "date": raw_date.strip("[]")})
    return nodes


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()

    for page_index in range(1, MAX_PAGES + 1):
        if page_index == 1:
            page_url = TARGET_URL
        else:
            page_url = f"{TARGET_URL}/{page_index}"

        try:
            response = session.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = "utf-8"
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        nodes = _parse_list_page(response.text)
        metrics.raw_item_count += len(nodes)
        if not nodes:
            break

        oldest_date_on_page = None

        for node in nodes:
            try:
                title = node.get("title", "").strip()
                href = node.get("href", "").strip()
                pub_at = parse_date(node.get("date", ""))

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(BASE_URL, href)
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

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
        if len(nodes) < PAGE_SIZE:
            break

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


def run():
    """执行抓取并统一保存。"""
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
