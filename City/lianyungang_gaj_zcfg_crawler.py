# -*- coding: utf-8 -*-
"""连云港市公安局_政策法规爬虫
目标栏目: http://gaj.lyg.gov.cn/sgaj/zcfg/zcfg.html
列表结构: TrueCMS，列表在隐藏容器 #initData 内(ul>li)，链接为 a[href]，日期在 span
分页说明: 平台 AJAX 翻页接口 /TrueCMS/messageController/getMessage.do 已停用(实测404)，
          列表页服务端渲染的首页数据(首屏60条)是唯一可用数据源。
"""
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


TARGET_URL = "http://gaj.lyg.gov.cn/sgaj/zcfg/zcfg.html"
SOURCE_NAME = "连云港市公安局_政策法规"
CATEGORY = "连云港"
BASE_URL = "http://gaj.lyg.gov.cn"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_RETRIES = 3

LIST_ITEM_SELECTOR = "ul.xxgk-list-con li"
LIST_FIRST_ONLY = False

DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日")

CONTENT_SELECTORS = [
    "#zoom",
    "#zoomcon",
    ".zoomcon",
    ".zoom",
    "#UCAP-CONTENT",
    ".page_con",
    "div.pp3",
    ".cont_mid",
    "div.txt",
    "div.wz",
    ".TRS_Editor",
    "#ivs_content",
    ".article-content",
    "#js_content",
    "#contains",
    ".art_content",
    ".pages_content",
    ".article",
    ".content",
]


def _fetch(session, url, timeout):
    """带重试的 HTTP GET，返回解码后的 HTML 文本"""
    last_error = None
    for _attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:
            last_error = exc
    raise last_error


def _extract_link_url(link):
    """从 a 标签提取链接：优先 href，回退 onclick=name2('...')"""
    href = (link.get("href") or "").strip()
    if href and not href.lower().startswith(("javascript:", "#")):
        return href
    onclick = link.get("onclick") or ""
    match = re.search(r"name2\(\s*'([^']+)'\s*\)", onclick)
    if match:
        return match.group(1).strip()
    return ""


def _parse_list_page(html, metrics):
    """解析列表页，返回 [{title, url, pub_at}]"""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#initData")
    if container is not None:
        nodes = container.select("ul li")
    elif LIST_FIRST_ONLY:
        first_node = soup.select_one(LIST_ITEM_SELECTOR)
        nodes = first_node.find_all("li", recursive=False) if first_node else []
    else:
        nodes = soup.select(LIST_ITEM_SELECTOR)
    metrics.raw_item_count += len(nodes)

    items = []
    for node in nodes:
        try:
            link = node.find("a")
            if link is None:
                metrics.invalid_item_count += 1
                continue

            title = (link.get("title") or "").strip()
            if not title:
                title = link.get_text(" ", strip=True)
            raw_href = _extract_link_url(link)

            span = node.find("span")
            span_text = span.get_text(" ", strip=True) if span else ""
            node_text = node.get_text(" ", strip=True)
            date_match = DATE_RE.search(span_text) or DATE_RE.search(node_text)
            pub_at = parse_date(date_match.group(0)) if date_match else None

            if not title or not raw_href:
                metrics.invalid_item_count += 1
                continue
            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"发布日期无法解析: {title[:30]}")
                continue

            article_url = urljoin(BASE_URL, raw_href)
            metrics.valid_item_count += 1
            items.append({"title": title, "url": article_url, "pub_at": pub_at})
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")
    return items


def _longest_text_block(soup):
    """启发式回退：取文本最长且无等长子节点的叶子容器"""
    best_text = ""
    for element in soup.find_all(["div", "td", "article", "section"]):
        text = element.get_text("\n", strip=True)
        if len(text) <= len(best_text):
            continue
        dominated = False
        for child in element.find_all(["div", "td", "article", "section"]):
            if len(child.get_text(" ", strip=True)) > len(text) * 0.85:
                dominated = True
                break
        if not dominated:
            best_text = text
    return best_text


def _extract_content(session, article_url, metrics):
    """提取详情页正文，失败记录错误并返回空串"""
    try:
        html = _fetch(session, article_url, DETAIL_TIMEOUT)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        for selector in CONTENT_SELECTORS:
            element = soup.select_one(selector)
            if element is None:
                continue
            text = element.get_text("\n", strip=True)
            if len(text) >= 30:
                return text

        fallback = _longest_text_block(soup)
        if fallback:
            return fallback
        metrics.errors.append(f"正文容器未命中: {article_url}")
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """返回 (policies, latest_items, metrics)"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    try:
        html = _fetch(session, TARGET_URL, LIST_TIMEOUT)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics

    items = _parse_list_page(html, metrics)
    if not items:
        metrics.errors.append("列表页未解析到有效记录")

    for item in items:
        latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

        if not is_target_date(item["pub_at"], target_from, target_to):
            metrics.filtered_count += 1
            continue

        content = _extract_content(session, item["url"], metrics)
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

    if items and items[-1]["pub_at"] >= target_from:
        metrics.errors.append(
            "首屏最旧记录不早于目标窗口起点，平台翻页接口已停用，可能存在更早数据未覆盖"
        )

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    ordered_latest = sorted(
        latest_items, key=lambda entry: entry["pub_at"], reverse=True
    )
    return policies, ordered_latest[:5], metrics


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult"""
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
