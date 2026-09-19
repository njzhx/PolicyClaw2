"""
苏州市虎丘区_政府发文爬虫
目标栏目：http://www.snd.gov.cn/hqqrmzf/zcwj/nav_list.shtml
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


TARGET_URL = "http://www.snd.gov.cn/hqqrmzf/zcwj/nav_list.shtml"
SOURCE_NAME = "苏州市虎丘区_政府发文"
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
        for selector in ('ucapcontent', '#TDContent', '.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.xlxlcont', '.Custom_UnionStyle', '.article', '.content', '.ewb-article-content'):
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
    import re
    import os
    import time
    from urllib.parse import urldefrag

    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    seen, signatures = set(), set()
    page, total_pages = 1, 1
    scope_excluded = 0
    while page <= total_pages:
        page_url = TARGET_URL if page == 1 else TARGET_URL.rsplit(".", 1)[0] + f"_{page}.shtml"
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 35 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 运行预算不足: {page_url}")
            break
        try:
            response = session.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            if page == 1:
                pagination = re.search(r"createPageHTML\('page_div',\s*(\d+)", response.text)
                if not pagination:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 官网分页总数缺失: {page_url}")
                else:
                    total_pages = int(pagination.group(1))
                if total_pages > 500:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 总页数超过安全上限: {page_url}")
                    total_pages = 500
            nodes = soup.select("li.datalist-item")
            if not nodes:
                metrics.errors.append(f"列表页抓取失败: 文章列表为空 {page_url}")
                break
            signature = tuple(str(n) for n in nodes)
            if signature in signatures:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {page_url}")
                break
            signatures.add(signature)
            metrics.raw_item_count += len(nodes)
            for node in nodes:
                try:
                    link, stamp = node.select_one("a[href]"), node.select_one("span.date")
                    title = (link.get("title") or link.get_text(" ", strip=True)).strip() if link else ""
                    href = str(link.get("href") or "").strip() if link else ""
                    pub_at = parse_date(stamp.get_text(strip=True)) if stamp else None
                    if not title or not href or not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表记录标题、链接或日期缺失: {page_url} {title}")
                        continue
                    article_url = urldefrag(urljoin(page_url, href))[0]
                    if article_url in seen:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen.add(article_url)
                    metrics.valid_item_count += 1
                    office = "党政办" in title or "政府办公室" in title
                    if office != ("政府办发文" in SOURCE_NAME):
                        scope_excluded += 1
                        continue
                    latest_items.append({"title": title, "pub_at": pub_at})
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue
                    policies.append({"title": title, "url": article_url, "pub_at": pub_at,
                                     "content": _extract_content(session, article_url, metrics),
                                     "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {page_url} - {exc}")
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
            break
        page += 1
    if scope_excluded:
        metrics.errors.append(f"[SCOPE_EXCLUDED] 另一发文机关记录未归入本入口: {scope_excluded} 条")
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
