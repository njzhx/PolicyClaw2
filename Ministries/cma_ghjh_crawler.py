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


TARGET_URL = "https://www.cma.gov.cn/zfxxgk/gknr/ghjh/"
SOURCE_NAME = "中国气象局_规划计划"
CATEGORY = "中央部委"
MAX_PAGES = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.cma.gov.cn/",
}


def _page_url(page_index):
    if page_index == 0:
        return TARGET_URL
    return urljoin(TARGET_URL, f"index_{page_index}.html")


def _get_soup(session, url, timeout):
    response = session.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.content.decode("utf-8", errors="replace"), "html.parser")


def _extract_content(session, article_url, metrics):
    try:
        soup = _get_soup(session, article_url, timeout=15)
        content_element = soup.select_one(
            "#rightBox div.scroll_main div.scroll_wrap div.scroll_cont"
        )
        if not content_element:
            metrics.errors.append(f"详情页未找到正文节点: {article_url}")
            return ""
        return content_element.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    candidates = []
    seen_urls = set()

    for page_index in range(MAX_PAGES):
        page_url = _page_url(page_index)
        try:
            soup = _get_soup(session, page_url, timeout=30)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
            break

        nodes = soup.select("#demo ul.mesgopen2.list > li")
        if not nodes:
            if page_index == 0:
                metrics.errors.append(
                    "未找到文章列表 #demo ul.mesgopen2.list > li"
                )
            break

        metrics.raw_item_count += len(nodes)
        for node in nodes:
            try:
                link = node.find("a")
                title = link.get_text(" ", strip=True) if link else ""
                href = (link.get("href") or "").strip() if link else ""
                date_element = node.select_one("font.date.endtime") or node.select_one(
                    "font.date"
                )
                pub_at = parse_date(
                    date_element.get_text(" ", strip=True) if date_element else ""
                )

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(
                        f"列表记录核心字段缺失: {page_url} - "
                        f"{title or href or '未知条目'}"
                    )
                    continue

                article_url = urljoin(page_url, href)
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)
                metrics.valid_item_count += 1
                candidates.append(
                    {"title": title, "url": article_url, "pub_at": pub_at}
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {page_url} - {exc}")

    candidates.sort(key=lambda item: item["pub_at"], reverse=True)
    latest_items = [
        {"title": item["title"], "pub_at": item["pub_at"]}
        for item in candidates[:5]
    ]

    for item in candidates:
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

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items, metrics


def run():
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
