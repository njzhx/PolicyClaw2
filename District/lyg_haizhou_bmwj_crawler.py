"""
连云港市海州区_部门信息公开爬虫
目标栏目：http://www.lyghz.gov.cn/lyghzqrmzf/xxgk/xxgk.html
说明：目录页提取部门子站链接，子站首页JS跳转至文件列表页
"""
import re
import time
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


TARGET_URL = "http://www.lyghz.gov.cn/lyghzqrmzf/xxgk/xxgk.html"
SOURCE_NAME = "连云港市海州区_部门信息公开"
CATEGORY = "连云港_海州区"

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



DEPARTMENT_SELECTOR = '.content td a[href]'
DEPARTMENT_PATH = '/hzq[^/]+/?'


def _load_remaining_nodes(session, list_url, html, initial_nodes, metrics, target_from=None):
    """Load records beyond the server-rendered first group from official jpage API."""
    total_match = re.search(r"totalRecord\s*:\s*(\d+)", html)
    column_match = re.search(r"columnId\s*:\s*['\"]([^'\"]+)['\"]", html)
    if not total_match or not column_match:
        return initial_nodes, False
    total = int(total_match.group(1))
    if total <= len(initial_nodes):
        return initial_nodes, True
    initial_dates = [next((value for value in
                           (parse_date(x.get_text(strip=True)) for x in node.select("span, #time"))
                           if value), None) for node in initial_nodes]
    initial_dates = [value for value in initial_dates if value]
    if (target_from and len(initial_dates) == len(initial_nodes)
            and initial_dates == sorted(initial_dates, reverse=True)
            and min(initial_dates) < target_from):
        return initial_nodes, True
    nodes = list(initial_nodes)
    complete = True
    stale_slots = 0
    endpoint = urljoin(list_url, "/TrueCMS/messageController/getMessage.do")

    def fetch_range(start, end):
        response = session.get(
            endpoint,
            params={"columnId": column_match.group(1), "startrecord": start,
                    "endrecord": end, "perpage": 15, "contentTemplate": ""},
            headers={**HEADERS, "Referer": list_url, "X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json().get("result", "")
        fragments = re.findall(r"<record><!\[CDATA\[(.*?)\]\]></record>", result, re.S)
        batch = [BeautifulSoup(fragment, "html.parser").select_one("li") for fragment in fragments]
        batch = [node for node in batch if node is not None]
        if batch or start == end:
            return batch, 0 if batch else 1
        middle = (start + end) // 2
        left, left_stale = fetch_range(start, middle)
        right, right_stale = fetch_range(middle + 1, end)
        return left + right, left_stale + right_stale

    for start in range(len(initial_nodes), total, 15):
        deadline = float(__import__("os").getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 20 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 列表分页预算不足: {list_url}")
            return nodes, False
        end = min(start + 14, total - 1)
        try:
            batch, stale = fetch_range(start, end)
            stale_slots += stale
            nodes.extend(batch)
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {endpoint} start={start} - {exc}")
            return nodes, False
    if stale_slots:
        metrics.errors.append(f"[STALE_INDEX] 官方总数含 {stale_slots} 个无记录索引: {list_url}")
    return nodes, complete


def scrape_data():
    import os
    import time
    from urllib.parse import urlsplit, urldefrag

    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    seen = set()
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        dept_links = []
        for link in soup.select(DEPARTMENT_SELECTOR):
            url = urljoin(TARGET_URL, str(link.get("href") or "").strip())
            # Department roots are directory destinations; guides/articles never qualify.
            if urlsplit(url).hostname == urlsplit(TARGET_URL).hostname and re.fullmatch(DEPARTMENT_PATH, urlsplit(url).path):
                if url not in dept_links:
                    dept_links.append(url)
        if not dept_links:
            metrics.errors.append(f"[CHANNEL_MISMATCH] 未发现可验证部门入口: {TARGET_URL}")
        for dept_url in dept_links:
            deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
            if deadline and time.time() + 35 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {dept_url}")
                break
            try:
                response = session.get(dept_url, headers=HEADERS, timeout=15)
                response.raise_for_status()
                match = re.search(r"location\.href\s*=\s*[\"']([^\"']+)[\"']", response.text)
                if not match:
                    metrics.errors.append(f"[CHANNEL_MISMATCH] 部门入口未提供已验证列表跳转: {dept_url}")
                    continue
                list_url = urljoin(dept_url, match.group(1))
                if urlsplit(list_url).hostname != urlsplit(TARGET_URL).hostname:
                    metrics.errors.append(f"[CHANNEL_MISMATCH] 部门跳转跨站: {list_url}")
                    continue
                response = session.get(list_url, headers=HEADERS, timeout=30)
                response.raise_for_status()
                page = BeautifulSoup(response.content, "html.parser")
                column = page.select_one('meta[name="ColumnName"]')
                column_name = str(column.get("content") or "") if column else ""
                if any(x in column_name for x in ("公开指南", "公开制度", "依申请公开")):
                    metrics.errors.append(f"[CHANNEL_MISMATCH] 部门跳转为非文章栏目: {column_name} {list_url}")
                    continue
                nodes = page.select("#initData li")
                if not nodes:
                    metrics.errors.append(f"列表页抓取失败: 未发现真实文章记录 {list_url}")
                    continue
                nodes, paging_complete = _load_remaining_nodes(
                    session, list_url, response.text, nodes, metrics, target_from
                )
                metrics.raw_item_count += len(nodes)
                page_dates = []
                for node in nodes:
                    try:
                        link = node.select_one("a[href]")
                        title = (link.get("title") or link.get_text(" ", strip=True)).strip() if link else ""
                        href = str(link.get("href") or "").strip() if link else ""
                        dates = [parse_date(x.get_text(strip=True)) for x in node.select("span, #time")]
                        pub_at = next((x for x in dates if x), None)
                        if not title or not href or not pub_at:
                            metrics.invalid_item_count += 1
                            metrics.errors.append(f"列表记录标题、链接或日期无效: {list_url} {title}")
                            continue
                        page_dates.append(pub_at)
                        article_url = urldefrag(urljoin(list_url, href))[0]
                        if article_url in seen:
                            metrics.duplicate_policy_count += 1
                            continue
                        seen.add(article_url)
                        metrics.valid_item_count += 1
                        latest_items.append({"title": title, "pub_at": pub_at})
                        if not is_target_date(pub_at, target_from, target_to):
                            metrics.filtered_count += 1
                            continue
                        policies.append({"title": title, "url": article_url, "pub_at": pub_at,
                                         "content": _extract_content(session, article_url, metrics),
                                         "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
                    except Exception as exc:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表记录解析失败: {list_url} - {exc}")
                covered = (len(page_dates) == len(nodes) and page_dates == sorted(page_dates, reverse=True)
                           and min(page_dates) < target_from)
                if not paging_complete and not covered:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 日期窗口超出静态列表，官网分页接口访问受阻: {list_url}")
            except Exception as exc:
                metrics.errors.append(f"部门列表页抓取失败: {dept_url} - {exc}")
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {TARGET_URL} - {exc}")
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not x.get("content") for x in policies)
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
