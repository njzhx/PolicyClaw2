"""
宿迁市人民政府_政策解读爬虫
目标栏目：https://www.suqian.gov.cn/cnsq/xxgkzcjd/xxgk_list.shtml
列表页结构：div.list ul li > a
分页机制：xxgk_list_{page}.shtml，每页16条
详情页日期节点：<publishtime>
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


TARGET_URL = "https://www.suqian.gov.cn/cnsq/xxgkzcjd/xxgk_list.shtml"
SOURCE_NAME = "宿迁市人民政府_政策解读"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_PAGES = 100


def _fetch(session, url, timeout=30):
    """带重试的HTTP请求"""
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout, proxies={"http": None, "https": None})
            response.raise_for_status()
            response.encoding = "utf-8"
            return response
        except Exception as exc:
            last_error = exc
    raise last_error


def _parse_list_page(soup):
    """解析列表页，返回记录列表"""
    records = []
    nodes = soup.select("div.list ul li")
    for node in nodes:
        link = node.select_one("a")
        if not link:
            continue
        href = link.get("href", "").strip()
        title = link.get_text(" ", strip=True)
        if not title or not href:
            continue
        records.append({
            "title": title,
            "href": href,
        })
    return records


def _extract_content(session, article_url, metrics):
    """提取详情页正文和发布日期"""
    try:
        response = _fetch(session, article_url, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # 提取发布日期 - 使用 publishtime 标签
        pub_at = None
        pub_elem = soup.select_one("publishtime")
        if pub_elem:
            pub_text = pub_elem.get_text(strip=True)
            pub_at = parse_date(pub_text)

        # 提取正文
        content = ""
        content_elem = soup.select_one("div.article")
        if content_elem:
            # 移除脚本、样式和无关元素
            for tag in content_elem.select("script, style, iframe"):
                tag.decompose()
            content = content_elem.get_text("\n", strip=True)

        return content, pub_at
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return "", None


def scrape_data():
    """抓取宿迁市人民政府政策解读数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()
    session.trust_env = False

    page_index = 1

    while page_index <= MAX_PAGES:
        if page_index == 1:
            page_url = TARGET_URL
        else:
            page_url = f"https://www.suqian.gov.cn/cnsq/xxgkzcjd/xxgk_list_{page_index}.shtml"

        try:
            response = _fetch(session, page_url, timeout=30)
            soup = BeautifulSoup(response.text, "html.parser")
            records = _parse_list_page(soup)
            metrics.raw_item_count += len(records)

            if not records:
                break

            # 检查是否需要停止翻页（通过最后一页的详情页日期判断）
            oldest_date_on_page = None

            for record in records:
                try:
                    title = record.get("title", "").strip()
                    href = record.get("href", "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    # 构建详情页URL
                    if href.startswith("/"):
                        article_url = f"https://www.suqian.gov.cn{href}"
                    elif not href.startswith("http"):
                        article_url = urljoin("https://www.suqian.gov.cn/", href)
                    else:
                        article_url = href

                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)

                    # 抓取详情页获取日期和正文
                    content, pub_at = _extract_content(session, article_url, metrics)

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                        continue

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    # 记录最旧日期用于翻页判断
                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    # 日期过滤
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

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

            # 翻页停止条件：最旧日期早于目标窗口起始日期
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            page_index += 1

        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

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
