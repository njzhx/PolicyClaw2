"""
南京市高淳区_政府办发文爬虫
目标栏目：http://www.njgc.gov.cn/gcqrmzf/214/223/index_18007.html
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


TARGET_URL = "http://www.njgc.gov.cn/gcqrmzf/214/223/index_18007.html"
SOURCE_NAME = "南京市高淳区_政府办发文"
CATEGORY = "南京_高淳区"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor'):
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



def _list_api_config(html):
    import re
    from urllib.parse import parse_qs, urlsplit
    def required(pattern):
        match = re.search(pattern, html)
        if not match:
            raise ValueError("[CHANNEL_MISMATCH] 官网列表参数缺失，拒绝全站检索")
        return match.group(1)
    site_meta = BeautifulSoup(html, "html.parser").select_one('meta[name="SiteName"]')
    if not site_meta or CATEGORY.split("_", 1)[1] not in site_meta.get("content", ""):
        raise ValueError("[CHANNEL_MISMATCH] 官网声明地域与爬虫区县不符")
    api = urljoin(TARGET_URL, required(r'websiteURL\s*=\s*["\']([^"\']+)'))
    if urlsplit(api).hostname != urlsplit(TARGET_URL).hostname:
        raise ValueError("[CHANNEL_MISMATCH] 列表接口跨站")
    site = required(r'var\s+siteId\s*=\s*(\d+)')
    channel = required(r'filter\[CHANNELID\].*?=\s*(\d+)')
    group = required(r'classinfoids\s*=\s*["\']([^"\']+)')
    category_key = parse_qs(urlsplit(TARGET_URL).query).get("tp", ["GROUPCAT"])[0]
    if category_key != "GROUPCAT":
        raise ValueError("[CHANNEL_MISMATCH] 未验证的分类参数")
    return api, {
        "index": required(r'index:\s*["\']([^"\']+)'),
        "type": required(r'type:\s*["\']([^"\']+)'),
        "siteId": site, "pageSize": int(required(r'var\s+pageSize\s*=\s*(\d+)')),
        "orderProperty": "DOCRELTIME", "orderDirection": "desc",
        "filter[SITEID]": site, "filter[CHANNELID]": channel, "filter[GROUPCAT]": group,
    }


def scrape_data():
    import os
    import time
    from datetime import datetime, timezone, timedelta
    from urllib.parse import urldefrag
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        api, params = _list_api_config(response.text)
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    seen, signatures = set(), set()
    for page in range(1, 501):
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 运行预算不足: {api} page={page}")
            break
        try:
            response = session.get(api, params={**params, "pageIndex": page, "pageNumber": page},
                                   headers=HEADERS, timeout=30)
            response.raise_for_status()
            payload = response.json()
            rows, count = payload["rows"], int(payload["count"])
            total_pages = int(payload["pageCount"])
            if not isinstance(rows, list) or count < 0 or total_pages < 0:
                raise ValueError("列表响应结构异常")
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {api} page={page} - {exc}")
            break
        if not rows:
            if (page - 1) * params["pageSize"] < count:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {api} page={page}")
            break
        signature = tuple(str(x.get("DOCPUBURL")) if isinstance(x, dict) else repr(x) for x in rows)
        if signature in signatures:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {api} page={page}")
            break
        signatures.add(signature)
        metrics.raw_item_count += len(rows)
        page_dates = []
        for row in rows:
            try:
                if (str(row.get("SITEID")) != params["siteId"]
                        or str(row.get("CHANNELID")) != params["filter[CHANNELID]"]
                        or str(row.get("GROUPCAT")) != params["filter[GROUPCAT]"]):
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"[CHANNEL_MISMATCH] 接口记录不属于请求栏目: {api} id={row.get('DOCID')}")
                    continue
                title = str(row.get("DOCTITLE") or "").strip()
                href = str(row.get("DOCPUBURL") or "").strip()
                raw_date = str(row.get("DOCRELTIME") or "")
                if raw_date.endswith("Z"):
                    raw_date = datetime.fromisoformat(raw_date).astimezone(timezone(timedelta(hours=8))).date()
                pub_at = parse_date(raw_date)
                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录字段缺失或日期无效: {href or api}")
                    continue
                page_dates.append(pub_at)
                url = urldefrag(urljoin(TARGET_URL, href))[0]
                if url in seen:
                    metrics.duplicate_policy_count += 1
                    continue
                seen.add(url)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue
                policies.append({"title": title, "url": url, "pub_at": pub_at,
                                 "content": _extract_content(session, url, metrics),
                                 "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {api} page={page} - {exc}")
        if page >= total_pages:
            break
        # The official API explicitly sorts DOCRELTIME descending. One old pinned item
        # cannot stop traversal; every record in the page must have a valid older date.
        if len(page_dates) == len(rows) and max(page_dates) < target_from:
            break
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {api}")
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not item.get("content") for item in policies)
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
