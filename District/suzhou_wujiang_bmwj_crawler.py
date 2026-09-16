"""
苏州市吴江区_部门发文爬虫
目标栏目：http://www.wujiang.gov.cn/zgwj/qzbmxxgk/xxgkml_ptlink.shtml
说明：目录页提取部门 channel_id，调用 /appsearch/wjapi/list 接口抓取
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


TARGET_URL = "http://www.wujiang.gov.cn/zgwj/qzbmxxgk/xxgkml_ptlink.shtml"
SOURCE_NAME = "苏州市吴江区_部门发文"
CATEGORY = "苏州_吴江区"

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
    "Referer": TARGET_URL,
}


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
        desc_meta = soup.select_one('meta[name="Description"]')
        if desc_meta and desc_meta.get("content"):
            return desc_meta["content"].strip()
        metrics.errors.append(f"正文选择器未命中: {article_url}")
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

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 提取部门 channel_id 和 deptid
        dept_params = []
        for a in soup.select("a[href*='xxgkbuml.shtml']"):
            href = a.get("href") or ""
            cm = re.search(r"channel_id=([a-f0-9]{32})", href)
            dm = re.search(r"deptid=([a-f0-9]+)", href)
            if cm:
                dept_params.append((cm.group(1), dm.group(1) if dm else ""))
        dept_params = list(dict.fromkeys(dept_params))
        metrics.raw_item_count = len(dept_params)

        for channel_id, deptid in dept_params:
            api_url = f"https://www.wujiang.gov.cn/appsearch/wjapi/list?deptid={deptid}"
            if channel_id:
                api_url += f"&channel_id={channel_id}"
            api_url += "&pageSize=20&currentPage=1"
            try:
                r = session.post(api_url, headers=API_HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
                payload = data.get("data") or {}
                items = payload.get("list") or []
                for item in items:
                    title = (item.get("title") or "").strip()
                    url = (item.get("url") or "").strip()
                    date_str = (item.get("qw_xcscrq") or item.get("published_time") or "").strip()
                    if not title or not url:
                        continue
                    pub_at = parse_date(date_str) if date_str else None
                    if not pub_at:
                        continue
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
            except Exception as exc:
                metrics.errors.append(f"部门接口失败 channel_id={channel_id}: {exc}")
                continue
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {exc}")

    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
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