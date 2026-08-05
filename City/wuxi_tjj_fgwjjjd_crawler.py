# -*- coding: utf-8 -*-
"""
无锡市统计局_法规文件及解读爬虫
目标网址: https://tj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml
主栏目汇总了法规文件(bmwj)和政策解读(zcjd)两个子栏目的内容
"""
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy

TARGET_URL = "https://tj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市统计局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "tj.wuxi.gov.cn"


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            if attempt == max_retries:
                raise


def _extract_content(article_url, metrics):
    """抓取详情页正文内容。"""
    try:
        html = _fetch_with_retry(article_url, timeout=15)

        # 尝试匹配正文容器: <div id="Zoom"> 或 <div class="content"> 或 TRS_UEDITOR
        patterns = [
            r'<div[^>]*id=["\']Zoom["\'][^>]*>(.*?)</div>',
            r'<div[^>]*class=["\'][^"\']*TRS_UEDITOR[^"\']*["\'][^>]*>(.*?)</div>',
            r'<div[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
            r'<div[^>]*id=["\']main[^"\']*["\'][^>]*>(.*?)</div>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                content_html = match.group(1)
                # 清理HTML标签，保留文本
                content = re.sub(r'<script[^>]*>.*?</script>', '', content_html, flags=re.DOTALL)
                content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                content = re.sub(r'<[^>]+>', '\n', content)
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = content.strip()
                if content:
                    return content

        # 备选：提取所有段落文本
        content_matches = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
        if content_matches:
            parts = []
            for p in content_matches:
                text = re.sub(r'<[^>]+>', '', p).strip()
                if text and len(text) > 10:
                    parts.append(text)
            if parts:
                return '\n'.join(parts)

        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_list_page(html):
    """解析列表页，提取 (title, href, raw_date) 三元组列表。

    页面结构示例:
    <ul class="list">
        <li>
            <a href="/doc/2026/05/11/4773129.shtml">标题</a>2026-05-11
        </li>
    </ul>
    或
    <div class="news_list">
        <dl>
            <dt><a href="...">标题</a></dt>
            <dd>2026-05-11</dd>
        </dl>
    </div>
    """
    records = []

    # 模式1: <li><a>标题</a>日期</li> 或 <li><a>标题</a><span>日期</span></li>
    li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL)
    for li_match in li_pattern.findall(html):
        # 提取链接
        link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', li_match)
        if not link_match:
            continue
        href = link_match.group(1).strip()
        title = link_match.group(2).strip()

        # 提取日期 - 可能在链接后、<span>中或独立文本
        raw_date = None
        date_patterns = [
            r'<span[^>]*>(\d{4}-\d{2}-\d{2})</span>',
            r'</a>(\d{4}-\d{2}-\d{2})',
            r'<font[^>]*>(\d{4}-\d{2}-\d{2})</font>',
        ]
        for dp in date_patterns:
            dm = re.search(dp, li_match)
            if dm:
                raw_date = dm.group(1)
                break

        if title and href and raw_date:
            records.append((title, href, raw_date))

    # 模式2: <dl><dt><a>标题</a></dt><dd>日期</dd></dl>
    if not records:
        dl_pattern = re.compile(r'<dl[^>]*>(.*?)</dl>', re.DOTALL)
        for dl_match in dl_pattern.findall(html):
            link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', dl_match)
            date_match = re.search(r'<dd[^>]*>(\d{4}-\d{2}-\d{2})</dd>', dl_match)
            if link_match and date_match:
                href = link_match.group(1).strip()
                title = link_match.group(2).strip()
                raw_date = date_match.group(1)
                if title and href:
                    records.append((title, href, raw_date))

    return records


def scrape_data():
    """抓取无锡市统计局法规文件及解读列表。"""
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
