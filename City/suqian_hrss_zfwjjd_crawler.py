"""
宿迁市人力资源和社会保障局_政府文件及解读爬虫
目标页面：https://sqhrss.suqian.gov.cn/rlzyj/zcwj/xxgk_list.shtml
列表结构：ul.listContent > li
正文容器：.article-content
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


TARGET_URL = "https://sqhrss.suqian.gov.cn/rlzyj/zcwj/xxgk_list.shtml"
SOURCE_NAME = "宿迁市人力资源和社会保障局_政府文件及解读"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}
PROXIES = {"http": None, "https": None}


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15, proxies=PROXIES)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        article = soup.select_one(".article-content")
        if article:
            for tag in article.find_all(["script", "style"]):
                tag.decompose()
            content = article.get_text("\n", strip=True)
        else:
            content = ""
        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取宿迁市人力资源和社会保障局政府文件及解读"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 1
    max_pages = 100
    all_items = []
    seen_urls = set()

    while page_index <= max_pages:
        page_url = TARGET_URL
        if page_index > 1:
            page_url = TARGET_URL.replace(
                "xxgk_list.shtml", f"xxgk_list_{page_index}.shtml"
            )

        try:
            response = session.get(page_url, headers=HEADERS, timeout=30, proxies=PROXIES)
            if response.status_code == 404:
                break
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.content, "html.parser")
            nodes = soup.select("ul.listContent > li")

            if not nodes:
                break

            new_nodes = []
            for node in nodes:
                link = node.select_one("a[href]")
                href = (link.get("href") or "").strip() if link else ""
                item_url = urljoin(page_url, href) if href else ""
                if item_url and item_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                if item_url:
                    seen_urls.add(item_url)
                new_nodes.append(node)

            if nodes and not new_nodes:
                metrics.errors.append(
                    f"列表第{page_index}页与已抓取页面重复，已停止翻页"
                )
                break

            all_items.extend(new_nodes)

            page_dates = []
            for node in nodes:
                text = node.get_text(" ", strip=True)
                import re
                match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
                if d := parse_date(match.group(1)) if match else None:
                    page_dates.append(d)

            if page_dates:
                oldest = min(page_dates)
                if oldest < target_from:
                    break

            if len(nodes) < 5:
                break

        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        page_index += 1

    metrics.raw_item_count = len(all_items)

    for node in all_items:
        try:
            link = node.select_one("a")
            if not link:
                metrics.invalid_item_count += 1
                continue

            href = (link.get("href") or "").strip()
            if not href or href.startswith("#") or "javascript" in href.lower():
                metrics.invalid_item_count += 1
                continue

            title = (link.get_text(" ", strip=True) or link.get("title")).strip()
            if not title:
                metrics.invalid_item_count += 1
                continue

            text = node.get_text(" ", strip=True)
            import re
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            pub_at = parse_date(date_match.group(1)) if date_match else None

            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"无法解析日期: {title[:30]}...")
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
