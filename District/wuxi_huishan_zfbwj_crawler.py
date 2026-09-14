"""
无锡市惠山区_政府办发文爬虫
目标栏目：http://www.huishan.gov.cn/zfxxgk/sqzfxxgkml/fgwjjjd/qzfbgswj/index.shtml
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


TARGET_URL = "http://www.huishan.gov.cn/zfxxgk/sqzfxxgkml/fgwjjjd/qzfbgswj/index.shtml"
SOURCE_NAME = "无锡市惠山区_政府办发文"
CATEGORY = "无锡_惠山区"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": TARGET_URL,
}


def _extract_api_params(session, metrics):
    """从页面提取API调用参数"""
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
        match = re.search(
            r'(?:qx_wbj_|qx_parent_wbj_)?getSearchList\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*["\'](\d+)["\']\s*,\s*["\'](\d+)["\']',
            html,
        )
        if match:
            return {
                "pageIndex": int(match.group(1)),
                "pageSize": int(match.group(2)),
                "siteId": match.group(3),
                "chanId": match.group(4),
            }
        match = re.search(
            r'(?:qx_wbj_|qx_parent_wbj_)?getSearchList\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\w+)',
            html,
        )
        if match:
            chan_var = match.group(4)
            chan_match = re.search(
                r'var\s+' + re.escape(chan_var) + r'\s*=\s*["\'](\d+)["\']',
                html,
            )
            return {
                "pageIndex": int(match.group(1)),
                "pageSize": int(match.group(2)),
                "siteId": match.group(3),
                "chanId": chan_match.group(1) if chan_match else "",
            }
        match2 = re.search(r'siteId["\']?\s*[:=]\s*["\']?(\d+)', html)
        chan_match = re.search(r'chanId["\']?\s*[:=]\s*["\']?(\d+)', html)
        if match2:
            return {
                "pageIndex": 1,
                "pageSize": 20,
                "siteId": match2.group(1),
                "chanId": chan_match.group(1) if chan_match else "",
            }
    except Exception as exc:
        metrics.errors.append(f"提取API参数失败: {exc}")
    return None


def _fetch_list_via_api(session, params, page, metrics):
    """通过API获取列表数据"""
    api_url = urljoin(TARGET_URL, "/intertidwebapp/govChanInfo/getBhDocuments")
    req_params = {
        "pageIndex": page,
        "pageSize": params.get("pageSize", 20),
        "siteId": params["siteId"],
        "chanId": params.get("chanId", ""),
        "ChannelType": 1,
        "KeyWord": "",
        "KeyWordType": "",
    }
    try:
        response = session.get(api_url, params=req_params, headers=API_HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("list", []), data.get("pageCount", 1)
    except Exception as exc:
        metrics.errors.append(f"API请求失败(page={page}): {exc}")
        return [], 0


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".wenZhang")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one(".pages_content")
            or soup.select_one("#zoom")
            or soup.select_one(".xlxlcont")
            or soup.select_one(".Custom_UnionStyle")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\\n", strip=True)
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

    api_params = _extract_api_params(session, metrics)
    if not api_params:
        metrics.errors.append("无法提取API参数，列表页可能使用静态HTML")
        return policies, latest_items[:5], metrics

    all_items = []
    page = 1
    while True:
        items, page_count = _fetch_list_via_api(session, api_params, page, metrics)
        if not items:
            break

        all_items.extend(items)
        if page >= page_count or page >= 10:
            break
        page += 1

    metrics.raw_item_count = len(all_items)

    for item in all_items:
        try:
            title = (item.get("title") or "").strip()
            href = (item.get("url") or "").strip()
            date_str = (item.get("writeTimeString") or "").strip()
            if not title or not href:
                metrics.invalid_item_count += 1
                continue

            pub_at = parse_date(date_str) if date_str else None
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