"""
苏州市姑苏区_政府发文爬虫
目标栏目：http://www.gusu.gov.cn/gsq/zcwj/xxgkml_zcwj_list.shtml
说明：列表通过 AJAX 接口 /szinf/getGsXxgkinfo/ 加载
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


TARGET_URL = "http://www.gusu.gov.cn/gsq/zcwj/xxgkml_zcwj_list.shtml"
SOURCE_NAME = "苏州市姑苏区_政府发文"
CATEGORY = "苏州_姑苏区"
DEPTCODE = "014152419"

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
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('.TRS_UEDITOR', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '#zoom', '.Custom_UnionStyle', '.article', '.content'):
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



import os
import re
import time
from urllib.parse import urlsplit, urldefrag

PAGE_SIZE = 500


def _is_office_file(document_number, title):
    number = str(document_number or "").replace(" ", "")
    return (number.startswith(("姑苏办", "苏名城保护办发"))
            or "府办" in number
            or bool(re.match(r"^(?:苏州市)?(?:姑苏区|区)?(?:人民)?政府办公室", title)))


def scrape_data():
    import os
    import time
    from urllib.parse import parse_qs, urlsplit, urldefrag
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    departments = [("014152419",)]
    scope_excluded = 0
    seen = set()
    api = urljoin(TARGET_URL, '/szinf/getGsXxgkinfo/')
    for department in departments:
        signatures = set()
        for page in range(1, 501):
            deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
            if deadline and time.time() + 35 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {api} department={department}")
                break
            params = {"deptcode": department[0]}
            params.update(pagesize=PAGE_SIZE, currpage=page)
            try:
                response = session.post(api, params=params, headers=HEADERS, timeout=30)
                response.raise_for_status()
                data = response.json()
                items, total = data.get("infolist") or [], int(data["totalcount"])
                if not isinstance(items, list) or total < 0:
                    raise ValueError("部门列表响应结构异常")
            except Exception as exc:
                metrics.errors.append(f"列表API抓取失败: {api} department={department} page={page} - {exc}")
                break
            if not items:
                if (page - 1) * 20 < total:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {api} department={department} page={page}")
                break
            signature = tuple(str(x.get('link')) if isinstance(x, dict) else repr(x) for x in items)
            if signature in signatures:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {api} department={department} page={page}")
                break
            signatures.add(signature)
            metrics.raw_item_count += len(items)
            dates = []
            for item in items:
                try:
                    title = str(item.get("title") or "").strip()
                    href = str(item.get('link') or "").strip()
                    pub_at = parse_date(item.get('addtime'))
                    if not title or not href or not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表记录字段缺失或日期无效: {href or api}")
                        continue
                    dates.append(pub_at)
                    if item.get("channel_name") in {"政府信息公开指南", "政府信息公开制度", "机构概况"}:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"[NON_ARTICLE] 排除非文章栏目 {item['channel_name']}: {href}")
                        continue
                    url = urldefrag(urljoin(TARGET_URL, href))[0]
                    if url in seen:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen.add(url)
                    metrics.valid_item_count += 1
                    # The authority API also returns budgets and meeting minutes.
                    # Only the government-file channel verified in official HTML
                    # belongs to these two file entry points.
                    if urlsplit(url).hostname != "www.gusu.gov.cn" or not urlsplit(url).path.startswith("/gsq/zfwj/"):
                        scope_excluded += 1
                        continue
                    document_number = str(item.get("c_wjbh") or "")
                    office = _is_office_file(document_number, title)
                    if office != ("政府办发文" in SOURCE_NAME):
                        scope_excluded += 1
                        continue
                    latest_items.append({"title": title, "pub_at": pub_at})
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue
                    policies.append({"title": title, "url": url, "pub_at": pub_at,
                                     "content": _extract_content(session, url, metrics),
                                     "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {api} department={department} - {exc}")
            if page * PAGE_SIZE >= total:
                break
        else:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {api} department={department}")
        if deadline and time.time() + 35 >= deadline:
            break
    if scope_excluded:
        metrics.errors.append(f"[SCOPE_EXCLUDED] 其他栏目或发文机关记录未归入本入口: {scope_excluded} 条")
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
