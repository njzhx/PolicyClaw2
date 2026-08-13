"""
宿迁市数据局_政策法规爬虫
目标栏目：https://xzspj.suqian.gov.cn/sxzspj/zcfg/xxgk_list.shtml
列表结构：ul.listContent > li，每个li包含a[href]链接和YYYY-MM-DD发布日期
正文容器：#UCAPCONTENT 或 .TRS_Editor
分页：xxgk_list_N.shtml
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


TARGET_URL = "https://xzspj.suqian.gov.cn/sxzspj/zcfg/xxgk_list.shtml"
SOURCE_NAME = "宿迁市数据局_政策法规"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _is_valid_zcfg_link(href):
    """判断是否为有效的政策法规详情链接"""
    if not href:
        return False
    excluded_keywords = ("apply", "sqgk", "ysqgk", "login", "register")
    href_lower = href.lower()
    if any(kw in href_lower for kw in excluded_keywords):
        return False
    return "/sxzspj/zcfg/" in href


def _extract_content(session, article_url, metrics):
    """抓取详情页正文"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        content_elem = soup.select_one(".article-content")
        if not content_elem:
            content_elem = soup.select_one("#UCAPCONTENT")
        if not content_elem:
            content_elem = soup.select_one(".TRS_Editor")

        if not content_elem:
            return ""

        for tag_name in ("script", "style", "noscript"):
            for node in content_elem.find_all(tag_name):
                node.decompose()

        text = content_elem.get_text("\n", strip=True)
        text = re.sub(r"[ \t\u00a0\u3000]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        noise_texts = {"关闭本页", "打印本页", "返回顶部", "网站地图"}
        for noise in noise_texts:
            text = text.replace(noise, "")

        return text.strip()
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取宿迁市数据局政策法规列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    try:
        nodes = []
        for page_index in range(1, 101):
            page_url = TARGET_URL
            if page_index > 1:
                page_url = TARGET_URL.replace(
                    "xxgk_list.shtml", f"xxgk_list_{page_index}.shtml"
                )
            resp = session.get(page_url, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            page_nodes = BeautifulSoup(
                resp.content, "html.parser"
            ).select("ul.listContent > li")
            if not page_nodes:
                break
            nodes.extend(page_nodes)

            page_dates = []
            for node in page_nodes:
                match = re.search(
                    r"(\d{4}-\d{2}-\d{2})", node.get_text(" ", strip=True)
                )
                if match:
                    value = parse_date(match.group(1))
                    if value:
                        page_dates.append(value)
            if page_dates and max(page_dates) < target_from:
                break

        if not nodes:
            metrics.errors.append("列表页未找到 ul.listContent > li")
            return policies, latest_items, metrics

        metrics.raw_item_count = len(nodes)

        for node in nodes:
            try:
                link = node.select_one("a")
                if not link:
                    metrics.invalid_item_count += 1
                    continue

                href = (link.get("href") or "").strip()

                if not _is_valid_zcfg_link(href):
                    metrics.invalid_item_count += 1
                    continue

                title = link.get_text(" ", strip=True).strip()
                title = re.sub(r"[ \t\u00a0\u3000]+", " ", title)
                title = re.sub(r"\s+", " ", title)

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                full_text = node.get_text(" ", strip=True)
                date_pattern = re.search(r"(\d{4}-\d{2}-\d{2})", full_text)
                pub_at = None
                if date_pattern:
                    pub_at = parse_date(date_pattern.group(1))

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                    continue

                article_url = urljoin(TARGET_URL, href)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

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

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )

    return policies, latest_items[:5], metrics


def run():
    """执行抓取并保存数据"""
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
