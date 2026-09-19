"""
苏州市太仓市_部门信息公开爬虫
目标栏目：http://www.taicang.gov.cn/taicang/qzbmxxgk/xxgkml_ptlink.shtml
说明：目录页提取部门链接，访问部门页提取 pid，调用 /appsearch/tcapi/list 接口
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


TARGET_URL = "http://www.taicang.gov.cn/taicang/qzbmxxgk/xxgkml_ptlink.shtml"
SOURCE_NAME = "苏州市太仓市_部门信息公开"
CATEGORY = "苏州_太仓市"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
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



def _extract_pid_from_dept_page(session, dept_url, metrics):
    try:
        r = session.get(dept_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        html = r.text
        m = re.search(r'(?:pid|channelId)\s*=\s*"([a-f0-9]{32})"', html)
        if m:
            return m.group(1)
    except Exception as exc:
        metrics.errors.append(f"部门页pid提取失败: {dept_url} - {exc}")
    return None


import os
import time
import re
from urllib.parse import parse_qs, urlsplit, urldefrag

DEPARTMENT_SELECTOR = 'a[href*="xxgkml.shtml?"]'
LIST_API = '/appsearch/tcapi/list'

def scrape_data():
    import os
    import time
    from urllib.parse import parse_qs, urlsplit, urldefrag
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        departments = []
        for link in soup.select(DEPARTMENT_SELECTOR):
            href = urljoin(TARGET_URL, str(link.get("href") or ""))
            if urlsplit(href).hostname == urlsplit(TARGET_URL).hostname and parse_qs(urlsplit(href).query).get("deptid"):
                if href not in departments:
                    departments.append(href)
        if not departments:
            raise ValueError("导航页未提取到部门链接")
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    seen = set()
    api = urljoin(TARGET_URL, LIST_API)
    for department_url in departments:
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {department_url}")
            break
        try:
            response = session.get(department_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            match = re.search(r'channelId\s*=\s*["\']([a-f0-9]{32})["\']', response.text)
            if not match:
                raise ValueError("部门页未声明栏目ID")
            response = session.get(urljoin(TARGET_URL, "/szinf/getChannelList/"),
                                   params={"is_show": "show", "channelid": match.group(1)},
                                   headers=HEADERS, timeout=15)
            response.raise_for_status()
            channels = response.json().get("channellist") or []
            ids = [x["channel_id"] for x in channels if x.get("channel_name") == "法定主动公开内容"]
            if len(ids) != 1:
                raise ValueError("未找到唯一法定主动公开内容栏目；禁止无栏目检索")
            department = (ids[0],)
        except Exception as exc:
            metrics.errors.append(f"部门列表页抓取失败: {department_url} - {exc}")
            continue
        signatures = set()
        for page in range(1, 501):
            deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
            if deadline and time.time() + 35 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {api} department={department}")
                break
            params = dict(zip(('pid',), department))
            params.update(pageSize=15, currentPage=page)
            try:
                response = session.post(api, params=params, headers=HEADERS, timeout=30)
                response.raise_for_status()
                data = response.json()
                payload = data["data"]
                items, total = payload.get("list") or [], int(payload["allRow"])
                if not isinstance(items, list) or total < 0:
                    raise ValueError("部门列表响应结构异常")
            except Exception as exc:
                metrics.errors.append(f"列表API抓取失败: {api} department={department} page={page} - {exc}")
                break
            if not items:
                if (page - 1) * 15 < total:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {api} department={department} page={page}")
                break
            signature = tuple(str(x.get('url')) if isinstance(x, dict) else repr(x) for x in items)
            if signature in signatures:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {api} department={department} page={page}")
                break
            signatures.add(signature)
            metrics.raw_item_count += len(items)
            dates = []
            for item in items:
                try:
                    title = str(item.get("title") or "").strip()
                    href = str(item.get('url') or "").strip()
                    pub_at = parse_date(item.get('published_time'))
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
            if page * 15 >= total:
                break
            if len(dates) == len(items) and dates == sorted(dates, reverse=True) and max(dates) < target_from:
                break
        else:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {api} department={department}")
        if deadline and time.time() + 35 >= deadline:
            break
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