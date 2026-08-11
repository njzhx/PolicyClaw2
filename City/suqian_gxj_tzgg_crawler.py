"""
宿迁市工业和信息化局_通知公告爬虫
目标栏目：https://gxj.suqian.gov.cn/sjxw/tzgg/list.shtml
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


TARGET_URL = "https://gxj.suqian.gov.cn/sjxw/tzgg/list.shtml"
SOURCE_NAME = "宿迁市工业和信息化局_通知公告"
CATEGORY = "宿迁"
BASE_URL = "https://gxj.suqian.gov.cn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
MAX_PAGES = 100


def _clean_date_text(date_text):
    """清理日期文本中的方括号"""
    if not date_text:
        return ""
    text = date_text.strip()
    text = text.lstrip("[").rstrip("]")
    return text.strip()


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        content_elem = soup.select_one("div.article")
        if not content_elem:
            metrics.errors.append(f"未找到正文容器: {article_url}")
            return ""

        for noise in content_elem.select("script, style, noscript"):
            noise.decompose()

        text = content_elem.get_text("\n", strip=True)

        text = re.sub(r"[\t ]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    page_index = 1
    oldest_date_on_page = None

    try:
        while page_index <= MAX_PAGES:
            if page_index == 1:
                page_url = TARGET_URL
            else:
                page_url = TARGET_URL.replace("/list.shtml", f"/list_{page_index}.shtml")

            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败 (第{page_index}页): {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")

            channel_list = soup.select_one(".channelList")
            if not channel_list:
                break

            items = channel_list.select("li.texto")
            if not items:
                break

            if page_index == 1:
                metrics.raw_item_count = len(items)

            for item in items:
                link_elem = item.select_one("a")
                if not link_elem:
                    metrics.invalid_item_count += 1
                    continue

                title = link_elem.get_text(strip=True)
                href = link_elem.get("href", "").strip()
                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(BASE_URL, href)

                date_elem = item.select_one("span")
                raw_date = ""
                pub_at = None
                if date_elem:
                    raw_date = date_elem.get_text(strip=True)
                    raw_date = _clean_date_text(raw_date)
                    pub_at = parse_date(raw_date)

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {title[:30]}... (raw: {raw_date})")
                    continue

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

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

            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"抓取过程异常: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

    return policies, latest_items[:5], metrics


def run():
    """运行爬虫"""
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
