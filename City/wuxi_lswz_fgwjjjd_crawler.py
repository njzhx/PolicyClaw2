# -*- coding: utf-8 -*-
"""
无锡市粮食和物资储备局_法规文件及解读爬虫
目标网址: https://lswz.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml
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


TARGET_URL = "https://lswz.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市粮食和物资储备局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "lswz.wuxi.gov.cn"


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    session = requests.Session()
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()

            # 编码处理：优先使用响应头中的 charset
            content_type = response.headers.get("Content-Type", "")
            if "charset" in content_type.lower():
                # 从 Content-Type 中提取编码
                encoding = response.headers.get_content_charset()
                if encoding:
                    response.encoding = encoding
            else:
                # 响应头无编码时使用 apparent_encoding
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
        selectors = [
            "div.content",
            "div.article",
            "div#zoom",
            "div#main",
            "divTRS_UEDITOR",
            "div[class*=content]",
            "div[class*=article]",
        ]

        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                # 清理脚本和样式
                for tag in element.find_all(["script", "style"]):
                    tag.decompose()
                content = element.get_text("\n", strip=True)
                if len(content) > 50:
                    return content

        # 备选：提取所有段落
        paragraphs = soup.find_all("p")
        if paragraphs:
            parts = []
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text and len(text) > 10:
                    parts.append(text)
            if parts:
                return "\n".join(parts)

        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_list_page(html):
    """解析列表页，提取 (title, href, raw_date) 三元组列表。

    页面结构示例:
    <li>
        <a href="/doc/2026/01/23/4777765.shtml">关于调整...</a>2026-01-23
    </li>
    """
    records = []
    soup = BeautifulSoup(html, "html.parser")

    # 查找列表容器 - 使用 li 元素
    li_elements = soup.select("ul.list li, ul.news_list li, div.list li")

    for li in li_elements:
        # 提取链接和标题
        link = li.select_one("a")
        if not link:
            continue

        href = link.get("href", "").strip()
        title = link.get_text(strip=True)

        # 提取日期 - 可能在链接后或 span 中
        raw_date = None

        # 尝试在链接后直接获取日期
        date_text = link.next_sibling
        if date_text:
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(date_text))
            if date_match:
                raw_date = date_match.group(1)

        # 尝试从 span 中获取日期
        if not raw_date:
            date_span = li.select_one("span, font")
            if date_span:
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_span.get_text())
                if date_match:
                    raw_date = date_match.group(1)

        # 尝试从整个 li 文本中匹配日期
        if not raw_date:
            li_text = li.get_text()
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", li_text)
            if date_match:
                raw_date = date_match.group(1)

        if title and href and raw_date:
            records.append((title, href, raw_date))

    # 如果上面没有找到，尝试正则表达式方式
    if not records:
        li_pattern = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL)
        for li_match in li_pattern.findall(html):
            link_match = re.search(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', li_match
            )
            if not link_match:
                continue
            href = link_match.group(1).strip()
            title = link_match.group(2).strip()

            # 提取日期
            raw_date = None
            date_patterns = [
                r"</a>(\d{4}-\d{2}-\d{2})",
                r"<span[^>]*>(\d{4}-\d{2}-\d{2})</span>",
                r"<font[^>]*>(\d{4}-\d{2}-\d{2})</font>",
            ]
            for dp in date_patterns:
                dm = re.search(dp, li_match)
                if dm:
                    raw_date = dm.group(1)
                    break

            if title and href and raw_date:
                records.append((title, href, raw_date))

    return records


def scrape_data():
    """抓取无锡市粮食和物资储备局法规文件及解读列表。"""
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
                        metrics.errors.append(
                            f"无法解析日期: {title[:30] if title else '无标题'} - raw_date={raw_date}"
                        )
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

                    policies.append(
                        {
                            "title": title,
                            "url": article_url,
                            "pub_at": pub_at,
                            "content": content,
                            "selected": False,
                            "category": CATEGORY,
                            "source": SOURCE_NAME,
                        }
                    )
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
