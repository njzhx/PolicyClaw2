# -*- coding: utf-8 -*-
"""
常州市人民政府_政府工作报告爬虫

目标网址: https://www.changzhou.gov.cn/gi_class/zwgk_05?furl=zfgzbg&cache=no&test=bbb
数据来源: 通过ajax接口获取 /ns_class/zwgk_18 的JSON响应中的HTML表格
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


TARGET_URL = "https://www.changzhou.gov.cn/ns_class/zwgk_18"
SOURCE_NAME = "常州市人民政府_政府工作报告"
CATEGORY = "常州"

# AJAX接口配置
AJAX_BASE_URL = "https://www.changzhou.gov.cn/ns_class/zwgk_18"
# 政府工作报告的gi参数值
GI_VALUE = "w2w7sv77"
# 固定的URL参数
ACT_ACT = "ajax"
T_PARAM = "1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": TARGET_URL,
}


def _fetch_with_retry(url, session, params=None, max_retries=3, timeout=30):
    """带重试的HTTP请求"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=timeout,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                continue
    raise last_error


def _parse_list_page(session, page=1):
    """解析列表页的JSON响应，返回文章列表"""
    params = {
        "act": ACT_ACT,
        "gi": GI_VALUE,
        "ns": "",
        "dept": "1",
        "notime": "",
        "page": str(page),
        "key": "",
        "t": T_PARAM,
        "cache": "no",
        "test": "bbb",
    }

    response = _fetch_with_retry(
        AJAX_BASE_URL, session, params=params, timeout=30
    )
    data = response.json()

    if data.get("status") != 10:
        return []

    html = data.get("html", "")
    soup = BeautifulSoup(html, "html.parser")

    articles = []
    rows = soup.find_all("tr")
    for row in rows:
        link_tag = row.find("a")
        if not link_tag:
            continue

        title = link_tag.get("title", "") or link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        if not title or not href:
            continue

        # 日期在第二个td中
        tds = row.find_all("td")
        raw_date = tds[1].get_text(strip=True) if len(tds) >= 2 else ""

        articles.append({
            "title": title,
            "href": href,
            "raw_date": raw_date,
        })

    return articles


def _extract_content(session, article_url, metrics):
    """提取详情页正文内容"""
    try:
        response = _fetch_with_retry(
            article_url, session, timeout=15
        )
        soup = BeautifulSoup(response.content, "html.parser")

        element = soup.select_one("td.GovInfoContent")
        if not element:
            return ""
        for noise in element.select("script, style, nav, header, footer"):
            noise.decompose()
        return element.get_text("\n", strip=True)
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
        # 获取第一页数据
        articles = _parse_list_page(session, page=1)
        metrics.raw_item_count = len(articles)

        if not articles:
            metrics.errors.append("列表页未返回任何数据")
            return policies, latest_items, metrics

        # 记录最旧日期用于判断是否需要翻页
        oldest_date_on_page = None

        for article in articles:
            try:
                title = article["title"]
                href = article["href"]
                raw_date = article["raw_date"]

                # 解析日期
                pub_at = parse_date(raw_date) if raw_date else None

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析日期: {title[:30]}... (raw: {raw_date})")
                    continue

                # 构建绝对URL
                article_url = urljoin("https://www.changzhou.gov.cn/", href)

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

        # 政府工作报告通常每年只有1-2条，总量很少，不进行翻页
        # 只需判断第一页是否完整即可

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
