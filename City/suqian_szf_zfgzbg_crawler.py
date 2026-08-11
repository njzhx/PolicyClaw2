# -*- coding: utf-8 -*-
"""
宿迁市人民政府_政府工作报告爬虫

目标网址: https://www.suqian.gov.cn/cnsq/zfgzbg/xxgk_list.shtml
数据来源: 服务端渲染 HTML 列表页，详情页为 HTML 正文
"""
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


TARGET_URL = "https://www.suqian.gov.cn/cnsq/zfgzbg/xxgk_list.shtml"
SOURCE_NAME = "宿迁市人民政府_政府工作报告"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch_with_retry(url, session, timeout=30, max_retries=3):
    """带重试的 HTTP 请求"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(
                url,
                headers=HEADERS,
                timeout=timeout,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                continue
    raise last_error


def _extract_content(session, article_url, metrics):
    """提取详情页正文内容"""
    try:
        response = _fetch_with_retry(article_url, session, timeout=15)
        soup = BeautifulSoup(response.content, "html.parser")

        # 查找正文容器
        content_elem = soup.select_one("div.xxgkcont")
        if not content_elem:
            metrics.errors.append(f"未找到正文容器: {article_url}")
            return ""

        # 移除脚本和样式
        for tag in content_elem.select("script, style"):
            tag.decompose()

        # 获取正文文本，保留段落边界
        content = content_elem.get_text("\n", strip=True)
        return content if content else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取政府工作报告数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()

    session = requests.Session()

    try:
        response = _fetch_with_retry(TARGET_URL, session, timeout=30)
        soup = BeautifulSoup(response.content, "html.parser")

        # 查找列表容器
        list_container = soup.select_one("div.list > ul.listContent")
        if not list_container:
            metrics.errors.append("未找到列表容器 div.list > ul.listContent")
            return policies, latest_items, metrics

        # 查找所有列表项
        list_items = list_container.select("li")
        metrics.raw_item_count = len(list_items)

        if not list_items:
            metrics.errors.append("列表页未返回任何数据")
            return policies, latest_items, metrics

        # 记录最旧日期用于判断是否需要翻页
        oldest_date_on_page = None

        for item in list_items:
            try:
                # 提取标题和链接
                link_tag = item.select_one("a")
                if not link_tag:
                    metrics.invalid_item_count += 1
                    continue

                title = link_tag.get_text(strip=True)
                href = link_tag.get("href", "")
                title_attr = link_tag.get("title", "")

                # 优先使用 title 属性作为标题
                if title_attr:
                    title = title_attr

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                # 提取日期
                date_span = item.select_one("span")
                raw_date = date_span.get_text(strip=True) if date_span else ""

                if not raw_date:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法获取日期: {title[:30]}...")
                    continue

                pub_at = parse_date(raw_date)
                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析日期: {title[:30]}... (raw: {raw_date})")
                    continue

                # 构建绝对 URL
                article_url = urljoin("https://www.suqian.gov.cn/", href)

                # 检查重复
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                # 记录最旧日期
                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                # 日期窗口过滤
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 提取正文
                content = _extract_content(session, article_url, metrics)

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

        # 政府工作报告每年只有1条，总量很少，不需要翻页
        # 判断是否需要翻页：如果最旧日期早于目标窗口起始日期，停止翻页
        # 由于是年度数据，通常只有一页

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


def run():
    """执行爬虫"""
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
