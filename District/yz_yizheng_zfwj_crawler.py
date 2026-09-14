"""
扬州市仪征市_政府发文爬虫
目标栏目：http://www.yizheng.gov.cn/zfxxgk/fdzdgknr/zfwj/zffwj/index.html
说明：通过 jpaas-publish-server 接口获取数据
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


TARGET_URL = "http://www.yizheng.gov.cn/zfxxgk/fdzdgknr/zfwj/zffwj/index.html"
SOURCE_NAME = "扬州市仪征市_政府发文"
CATEGORY = "扬州_仪征市"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_jpaas_params(session, metrics):
    """从页面 script 标签提取 jpaas 参数"""
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
        # 找 queryData
        m = re.search(r'queryData\s*=\s*"([^"]+)"', html)
        if m:
            query_str = m.group(1)
            params = {}
            for pm in re.finditer(r"'(\w+)':\s*'([^']*)'", query_str):
                params[pm.group(1)] = pm.group(2)
            return params
        metrics.errors.append("未找到 jpaas queryData")
    except Exception as exc:
        metrics.errors.append(f"提取jpaas参数失败: {exc}")
    return None


def _fetch_list(session, params, page, metrics):
    base = TARGET_URL.split("/zfxxgk/")[0] if "/zfxxgk/" in TARGET_URL else TARGET_URL.rsplit("/", 1)[0]
    api_url = urljoin(base, "/api-gateway/jpaas-publish-server/front/page/build/unit")
    params["pageNo"] = str(page)
    try:
        r = session.get(api_url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        html = data.get("data", {}).get("html", "")
        return html, data.get("data", {}).get("count", 0)
    except Exception as exc:
        metrics.errors.append(f"接口请求失败(page={page}): {exc}")
        return "", 0


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
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

    params = _extract_jpaas_params(session, metrics)
    if not params:
        return policies, latest_items[:5], metrics

    page = 1
    max_pages = 10
    while page <= max_pages:
        html, total = _fetch_list(session, params, page, metrics)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("li.clearfix")
        if not items:
            break

        oldest_on_page = None
        for li in items:
            link = li.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get("title") or link.get_text(" ", strip=True)
            title = title.strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            date_span = li.select_one("span")
            date_text = date_span.get_text(strip=True) if date_span else ""
            pub_at = parse_date(date_text) if date_text else None
            if not pub_at:
                metrics.invalid_item_count += 1
                continue
            if oldest_on_page is None or pub_at < oldest_on_page:
                oldest_on_page = pub_at
            article_url = urljoin(TARGET_URL, href)
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

        page_size = 15
        total_pages = (total + page_size - 1) // page_size if total else 0
        if page >= total_pages:
            break
        if oldest_on_page and oldest_on_page < target_from:
            break
        page += 1

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
