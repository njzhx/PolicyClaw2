"""
盐城市工信局_通知公告爬虫
目标栏目：https://gxj.yancheng.gov.cn/col/col1833/index.html
页面机制：汉风 jpage 系统，列表数据内嵌在 <script type="text/xml"> 的 datastore 中，
          翻页通过 /module/web/jpage/dataproxy.jsp 接口（参数从首页动态提取）。
记录格式：<li><a href="..." title="...">标题</a>[YYYY-MM-DD]</li>
详情页正文：#zoom
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


TARGET_URL = "https://gxj.yancheng.gov.cn/col/col1833/index.html"
SOURCE_NAME = "盐城市工信局_通知公告"
CATEGORY = "盐城"
BASE_URL = "https://gxj.yancheng.gov.cn"

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


def _fetch(session, url, timeout=LIST_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _extract_datastore_block(html):
    """从首页 HTML 提取内嵌 datastore XML 及翻页接口 URL 模板。"""
    block_match = re.search(
        r'<script type="text/xml"><datastore>(.*?)</datastore></script>',
        html,
        re.S,
    )
    if not block_match:
        return None, None
    block = block_match.group(1)
    next_match = re.search(
        r'<nextgroup><!\[CDATA\[<a href="([^"]+)"', block, re.S
    )
    next_url = next_match.group(1) if next_match else None
    return block, next_url


def _parse_records(block, metrics):
    """解析 datastore 中的 record 片段，返回 [{title, url, pub_at}]。"""
    fragments = re.findall(r"<record><!\[CDATA\[(.*?)\]\]></record>", block, re.S)
    metrics.raw_item_count += len(fragments)
    items = []
    for fragment in fragments:
        try:
            soup = BeautifulSoup(fragment, "html.parser")
            link = soup.select_one("a[href]")
            if not link:
                metrics.invalid_item_count += 1
                continue
            title = (link.get("title") or link.get_text(" ", strip=True)).strip()
            href = (link.get("href") or "").strip()
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", fragment)
            pub_at = parse_date(date_match.group(1)) if date_match else None
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
    """提取详情页正文；首个响应若为加速乐挑战页（过短且无正文容器）则重试一次。"""
    try:
        html = ""
        for _ in range(2):
            html = _fetch(session, article_url, timeout=DETAIL_TIMEOUT)
            if len(html) >= 2000 or "zoom" in html:
                break
            time.sleep(1)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        content_elem = (
            soup.select_one("#zoom")
            or soup.select_one(".zoom")
            or soup.select_one("#zoomcon")
            or soup.select_one("div.article")
            or soup.select_one("div.wp.article-content")
        )
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

    try:
        html = _fetch(session, TARGET_URL)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics

    block, next_url = _extract_datastore_block(html)
    if not block:
        metrics.errors.append("列表页解析失败：未找到内嵌 datastore")
        return policies, latest_items, metrics

    seen_urls = set()
    page_index = 1

    while block:
        items = _parse_records(block, metrics)
        if not items and page_index == 1:
            metrics.errors.append("列表页解析失败或无数据")
            break

        new_items = []
        for item in items:
            if item["url"] in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(item["url"])
            new_items.append(item)

        if items and not new_items:
            metrics.errors.append(
                f"列表第{page_index}页与已抓取页面重复，已停止翻页"
            )
            break

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
        if items and items[-1]["pub_at"] < target_from:
            break
        if not next_url or page_index >= MAX_PAGES:
            break

        page_url = urljoin(
            BASE_URL, re.sub(r"page=\d+", f"page={page_index}", next_url)
        )
        try:
            time.sleep(0.5)
            page_xml = _fetch(session, page_url)
        except Exception as exc:
            metrics.errors.append(f"列表翻页抓取失败: {page_url} - {exc}")
            break
        block = page_xml
        page_index += 1

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
