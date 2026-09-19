"""
徐州市沛县_政府办发文爬虫
目标栏目：http://www.px.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003195002&deptcode=001001
"""
import json
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


TARGET_URL = "http://www.px.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003195002&deptcode=001001"
SOURCE_NAME = "徐州市沛县_政府办发文"
CATEGORY = "徐州_沛县"
CATEGORYNUM = "003195002"
DEPTCODE = "001001"
BASE_DOMAIN = "http://www.px.gov.cn"

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
    "Content-Type": "application/json;charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": TARGET_URL,
}


def _extract_site_guid(session, metrics):
    try:
        js_url = urljoin(BASE_DOMAIN, "/js/ajaxReq.js")
        response = session.get(js_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        js_text = response.text
        match = re.search(r'"siteGuid"\s*:\s*"([a-f0-9\-]{36})"', js_text)
        if match:
            return match.group(1)
        metrics.errors.append("无法从ajaxReq.js提取siteGuid")
    except Exception as exc:
        metrics.errors.append(f"提取siteGuid失败: {exc}")
    return None


def _fetch_list_via_api(session, site_guid, page, metrics):
    api_url = urljoin(BASE_DOMAIN, "/EWB-FRONT/rest/lightfrontaction/getgovinfolist")
    payload = {
        "deptcode": DEPTCODE,
        "categorynum": CATEGORYNUM,
        "pageIndex": page,
        "pageSize": 20,
        "siteGuid": site_guid,
    }
    try:
        response = session.post(
            api_url, json=payload, headers=API_HEADERS, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        custom = data.get("custom", {})
        items = custom.get("data", [])
        total = custom.get("total", 0)
        page_count = (total + 19) // 20 if total else 0
        return items, page_count
    except Exception as exc:
        metrics.errors.append(f"API请求失败(page={page}): {exc}")
        return [], 0


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('#ivs_content', '.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.xlxlcont', '.Custom_UnionStyle', '.ewb-article-content', '.article'):
            original = soup.select_one(selector)
            if original is None:
                continue
            # Work on a copy so overlapping fallback containers remain intact.
            element = BeautifulSoup(str(original), "html.parser")
            for extra in element.select("script, style"):
                extra.decompose()
            has_media = bool(element.select("img, object, embed"))
            for link in element.select("a[href]"):
                path = link.get("href", "").split("?", 1)[0].split("#", 1)[0].lower()
                if path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")):
                    has_media = True
                    link.decompose()
            text = element.get_text("\n", strip=True)
            if text:
                return text
            media_only = media_only or has_media
            if has_media:
                break
        if media_only:
            desc = soup.select_one('meta[name="Description"], meta[name="description"]')
            summary = str(desc.get("content") or "").strip() if desc else ""
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            title_meta = soup.select_one('meta[name="ArticleTitle"]')
            article_title = str(title_meta.get("content") or "").strip() if title_meta else ""
            if summary and summary not in {title, article_title}:
                return summary
            metrics.errors.append(f"[ATTACHMENT_ONLY] 图片/附件型页面无可提取网页正文: {article_url}")
        else:
            metrics.errors.append(f"[CONTENT_MISSING] 正文选择器未命中或正文为空: {article_url}")
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

    site_guid = _extract_site_guid(session, metrics)
    if not site_guid:
        return policies, sorted(latest_items, key=lambda x: x["pub_at"], reverse=True)[:5], metrics

    all_items = []
    page = 0
    max_pages = 100
    signatures = set()
    while page < max_pages:
        import os
        import time
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 运行预算不足: {TARGET_URL} page={page}")
            break
        items, page_count = _fetch_list_via_api(session, site_guid, page, metrics)
        if not items:
            break
        signature = tuple(str(x.get("infourl")) if isinstance(x, dict) else repr(x) for x in items)
        if signature in signatures:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {TARGET_URL} page={page}")
            break
        signatures.add(signature)
        all_items.extend(items)
        if page + 1 >= page_count or page_count == 0:
            break
        page += 1
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {TARGET_URL}")

    metrics.raw_item_count = len(all_items)

    seen_urls = set()
    for item in all_items:
        try:
            title = (item.get("title") or "").strip()
            infourl = (item.get("infourl") or "").strip()
            date_str = (item.get("infodate") or "").strip()
            if not title or not infourl:
                metrics.invalid_item_count += 1
                continue

            pub_at = parse_date(date_str) if date_str else None
            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"无法解析日期: {title[:30]}...")
                continue

            from urllib.parse import urldefrag
            article_url = urldefrag(urljoin(BASE_DOMAIN, infourl))[0]
            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)
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
    return policies, sorted(latest_items, key=lambda x: x["pub_at"], reverse=True)[:5], metrics


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
