"""
苏州市吴江区_政府发文爬虫
目标栏目：http://www.wujiang.gov.cn/zgwj/zcwj/wjxxgkml_zcwj.shtml
说明：列表通过 AJAX 接口 /appsearch/wjapi/list 加载
"""
from urllib.parse import urljoin

import requests

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "http://www.wujiang.gov.cn/zgwj/zcwj/wjxxgkml_zcwj.shtml"
SOURCE_NAME = "苏州市吴江区_政府发文"
CATEGORY = "苏州_吴江区"
DEPTID = "6d76797b89664714a9a3c8849cbf5d32"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": TARGET_URL,
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one("#zoom")
            or soup.select_one(".Custom_UnionStyle")
            or soup.select_one(".article")
            or soup.select_one(".content")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}

    page = 1
    max_pages = 10
    while page <= max_pages:
        api_url = (
            f"https://www.wujiang.gov.cn/appsearch/wjapi/list"
            f"?pageSize=20&currentPage={page}&deptid={DEPTID}"
        )
        try:
            response = session.post(api_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()
            payload = data.get("data") or {}
            items = payload.get("list") or []
            if not items:
                break

            oldest_on_page = None
            for item in items:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                date_str = (
                    item.get("qw_xcscrq")
                    or item.get("published_time")
                    or item.get("qw_xcgkrq")
                    or ""
                ).strip()
                if not title or not url:
                    metrics.invalid_item_count += 1
                    continue
                pub_at = parse_date(date_str) if date_str else None
                if not pub_at:
                    metrics.invalid_item_count += 1
                    continue
                if oldest_on_page is None or pub_at < oldest_on_page:
                    oldest_on_page = pub_at
                article_url = urljoin(TARGET_URL, url)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue
                policies.append({
                    "title": title,
                    "url": article_url,
                    "pub_at": pub_at,
                    "content": _extract_content(session, article_url, metrics),
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                })

            total_pages = payload.get("totalPage") or 0
            if page >= total_pages:
                break
            if oldest_on_page and oldest_on_page < target_from:
                break
            page += 1
        except Exception as exc:
            metrics.errors.append(f"列表页{page}抓取失败: {exc}")
            break

    metrics.raw_item_count = metrics.valid_item_count + metrics.invalid_item_count
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items[:5], metrics


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