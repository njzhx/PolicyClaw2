"""
苏州市虎丘区_部门发文爬虫
目标栏目：http://www.snd.gov.cn/hqqrmzf/dfbmptlj/gxqxxgkml_ptlink.shtml
说明：该页为部门目录，遍历各部门信息公开页（bmxxgk_list.shtml）抓取文件
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


TARGET_URL = "http://www.snd.gov.cn/hqqrmzf/dfbmptlj/gxqxxgkml_ptlink.shtml"
SOURCE_NAME = "苏州市虎丘区_部门发文"
CATEGORY = "苏州_虎丘区"

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
        for selector in ('ucapcontent', '#TDContent', '.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.Custom_UnionStyle', '.article', '.content'):
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
    import os
    import time
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    seen = set()
    try:
        from urllib.parse import parse_qs, urlsplit
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        departments = []
        for link in soup.select("a[href*='bmxxgk_list.shtml']"):
            query = parse_qs(urlsplit(link.get("href", "")).query)
            if query.get("deptid") and query.get("coltype"):
                departments.append((query["deptid"][0], query["coltype"][0]))
        departments = list(dict.fromkeys(departments))
        if not departments:
            raise ValueError("导航页未提取到部门链接")
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    for department in departments:
        signatures = set()
        for page in range(1, 501):
            deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
            if deadline and time.time() + 35 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {TARGET_URL}")
                break
            api = urljoin(TARGET_URL, "/appsearch/hqapi/list")
            try:
                response = session.post(api, data={"currentPage": page, "pageSize": 15,
                                        "deptid": department[0], "coltype": department[1],
                                        "channel_id": "dbadeb187b2c4f4b9997ee39043c0bd5", "firstid": ""},
                                        headers=HEADERS, timeout=30)
                response.raise_for_status()
                data = response.json()["data"]
                items, total = data["list"], int(data["allRow"])
            except Exception as exc:
                metrics.errors.append(f"列表API抓取失败: {api} department={department} page={page} - {exc}")
                break
            if not items:
                if (page - 1) * 15 < total:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {api} department={department} page={page}")
                break
            signature = tuple(str(x.get("url")) for x in items)
            if signature in signatures:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {api} department={department}")
                break
            signatures.add(signature)
            dates = []
            for item in items:
                metrics.raw_item_count += 1
                try:
                    title = str(item.get("title") or "").strip()
                    # Repeated identical title fragments are a CMS formatting defect.
                    for size in range(1, len(title) // 2 + 1):
                        if len(title) % size == 0 and title == title[:size] * (len(title) // size):
                            title = title[:size]
                            break
                    path = str(item.get("url") or "").strip()
                    pub_at = parse_date(item.get("published_time"))
                    if not title or not path or not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表记录缺少标题/链接/发布日期: {api} department={department}")
                        continue
                    metrics.valid_item_count += 1
                    dates.append(pub_at)
                    if item.get("channel_name") != "部门文件":
                        continue
                    article_url = urljoin(TARGET_URL, path)
                    if article_url in seen:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen.add(article_url)
                    latest_items.append({"title": title, "pub_at": pub_at})
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue
                    policies.append({"title": title, "url": article_url, "pub_at": pub_at,
                                     "content": _extract_content(session, article_url, metrics),
                                     "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {api} - {exc}")
            if page * 15 >= total:
                break
            if len(dates) == len(items) and max(dates) < target_from:
                break
        else:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门达到安全页数上限: {department}")
        if deadline and time.time() + 35 >= deadline:
            break
    session.close()
    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not x["content"] for x in policies)
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