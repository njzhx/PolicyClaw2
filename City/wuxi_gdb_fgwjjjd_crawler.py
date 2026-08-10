# -*- coding: utf-8 -*-
"""
无锡市国防动员办公室_法规文件及解读爬虫
目标网址: https://gdb.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml
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

TARGET_URL = "https://gdb.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市国防动员办公室_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "gdb.wuxi.gov.cn"


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            # 处理编码
            if response.apparent_encoding:
                response.encoding = response.apparent_encoding
            return response.text
        except Exception as exc:
            if attempt == max_retries:
                raise
            continue


def _extract_content(article_url, metrics):
    """抓取详情页正文内容。"""
    try:
        html = _fetch_with_retry(article_url, timeout=15)
        soup = BeautifulSoup(html, "html.parser")

        # 尝试多个可能的正文容器
        content = ""
        # 模式1: divTRS_Editor
        element = soup.select_one("divTRS_Editor, div.trs_editor, div#TRS_Editor")
        if element:
            content = element.get_text("\n", strip=True)

        # 模式2: div.content, div.article
        if not content:
            element = soup.select_one("div.content, div.article, div.detail_content")
            if element:
                content = element.get_text("\n", strip=True)

        # 模式3: 提取所有段落
        if not content:
            paras = soup.select("div.zoom p, div.content p, article p")
            if paras:
                parts = [p.get_text(strip=True) for p in paras if p.get_text(strip=True)]
                content = "\n".join(parts)

        return content if content else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_list_page(html):
    """解析列表页，提取 (title, href, raw_date) 三元组列表。

    页面结构示例:
    - [标题](https://gdb.wuxi.gov.cn/doc/2025/12/31/4710481.shtml)2025-12-31
    """
    records = []

    # 使用 BeautifulSoup 解析
    soup = BeautifulSoup(html, "html.parser")

    # 查找所有列表项中的链接
    for li in soup.select("ul li, div.list li"):
        link = li.select_one("a")
        if not link:
            continue

        href = link.get("href", "").strip()
        title = link.get_text(strip=True)

        # 跳过空标题
        if not title:
            continue

        # 提取日期 - 可能在链接后的文本中，格式为 YYYY-MM-DD
        raw_date = None
        # 方法1: 从链接文本末尾提取日期
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', title)
        if date_match:
            # 标题中可能包含日期，需要分离
            pass

        # 方法2: 查找链接后的文本节点
        link_text = link.get_text(strip=True)
        # 尝试从整个 li 文本中找日期
        li_text = li.get_text(strip=True)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', li_text)
        if date_match:
            raw_date = date_match.group(1)
            # 清理标题中的日期
            title = re.sub(r'\s*\d{4}-\d{2}-\d{2}\s*$', '', link_text).strip()
            title = title if title else link_text

        # 方法3: 直接解析 Markdown 链接格式
        # [标题](url)2025-12-31
        md_match = re.search(r'\[([^\]]+)\]\(([^)]+)\)(\d{4}-\d{2}-\d{2})', li_text)
        if md_match:
            title = md_match.group(1).strip()
            href = md_match.group(2).strip()
            raw_date = md_match.group(3)

        if title and href and raw_date:
            records.append((title, href, raw_date))

    return records


def scrape_data():
    """抓取无锡市国防动员办公室法规文件及解读列表。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index + 1}.shtml"

            html = _fetch_with_retry(page_url)
            nodes = _parse_list_page(html)

            if not nodes:
                break

            page_raw_count = len(nodes)
            metrics.raw_item_count += page_raw_count
            oldest_date_on_page = None

            for title, href, raw_date in nodes:
                try:
                    # 域名校验
                    if ALLOWED_HOST not in href and not href.startswith("/"):
                        metrics.invalid_item_count += 1
                        continue

                    pub_at = parse_date(raw_date)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析日期: {title[:30] if title else '无标题'} - raw_date={raw_date}")
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    # 去重
                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)

                    if not title:
                        metrics.invalid_item_count += 1
                        continue

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    # 日期过滤
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    # 抓取详情页内容
                    content = _extract_content(article_url, metrics)

                    policies.append({
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    })
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {exc}")

            # 分页提前终止：当前页最旧日期已早于目标窗口起始日期
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
            # 不足整页时终止
            if page_raw_count < 10:
                break
            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )

    return policies, latest_items[:5], metrics


def run():
    """执行抓取并保存数据。"""
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
