"""
淮安市盱眙县_政府办发文爬虫
目标栏目：http://www.xuyi.gov.cn/cmsweb/zwgk/xy/index.html?topic=2944&orgid=2c94939266aee97a0166b3aed87f04d1&zfwjtype=zfb
说明：通过 /articleCommonController/lists.do 接口获取数据
"""
from urllib.parse import urljoin, urlparse, parse_qs

import requests

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "http://www.xuyi.gov.cn/cmsweb/zwgk/xy/index.html?topic=2944&orgid=2c94939266aee97a0166b3aed87f04d1&zfwjtype=zfb"
SOURCE_NAME = "淮安市盱眙县_政府办发文"
CATEGORY = "淮安_盱眙县"
RDEPTID = "ff808081657f153d01657f48728c0071"
ZFWJ_TYPE = "zfb"
ZFWJ_NO = "盱政办发"
EXTRA_TYPE = ""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
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
        desc_meta = soup.select_one('meta[name="Description"]')
        if desc_meta and desc_meta.get("content"):
            return desc_meta["content"].strip()
        metrics.errors.append(f"正文选择器未命中: {article_url}")
        return ""

    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _fetch_list(session, page, metrics):
    parsed = urlparse(TARGET_URL)
    qs = parse_qs(parsed.query)
    topic = qs.get("topic", [""])[0]
    orgid = qs.get("orgid", [""])[0]

    post_data = {
        "page": page,
        "pagesize": 15,
        "topic": topic,
        "rdeptid": RDEPTID,
    }
    if orgid:
        post_data["deptid"] = orgid
    if EXTRA_TYPE:
        post_data["type"] = EXTRA_TYPE
    if ZFWJ_TYPE:
        post_data["zfwjtype"] = ZFWJ_TYPE
    if ZFWJ_NO:
        post_data["zfwjNo"] = ZFWJ_NO

    base = f"{parsed.scheme}://{parsed.netloc}"
    api_url = urljoin(base, "/articleCommonController/lists.do")
    try:
        r = session.post(api_url, data=post_data, headers=API_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        value = data.get("value") or {}
        return value.get("list") or [], value.get("total") or 0
    except Exception as exc:
        metrics.errors.append(f"API请求失败(page={page}): {exc}")
        return [], 0


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
        items, total = _fetch_list(session, page, metrics)
        if not items:
            break
        oldest_on_page = None
        for item in items:
            title = (item.get("title") or "").strip()
            path = (item.get("path") or "").strip()
            domain = (item.get("domain") or "").strip()
            date_str = (item.get("releaseTime") or "").strip()
            if not title or not path:
                metrics.invalid_item_count += 1
                continue
            pub_at = parse_date(date_str) if date_str else None
            if not pub_at:
                metrics.invalid_item_count += 1
                continue
            if oldest_on_page is None or pub_at < oldest_on_page:
                oldest_on_page = pub_at
            article_url = (domain.rstrip("/") + "/" + path.lstrip("/")) if domain else urljoin(TARGET_URL, path)
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
        total_pages = (total + 14) // 15 if total else 0
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
