"""
南通市启东市_政府发文爬虫
目标栏目：http://www.qidong.gov.cn/qdsrmzf/szfwj/szfwj.html
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


TARGET_URL = "http://www.qidong.gov.cn/qdsrmzf/szfwj/szfwj.html"
SOURCE_NAME = "南通市启东市_政府发文"
CATEGORY = "南通_启东市"

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
        for selector in ('.TRS_UEDITOR', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '#zoom', '.xlxlcont', '.Custom_UnionStyle', '.content'):
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
    from urllib.parse import urldefrag
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        nodes = soup.select("#initData ul.list-ul li")
        metrics.raw_item_count = len(nodes)
        if not nodes:
            raise ValueError("列表页未解析到记录")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    seen, dates = set(), []
    for li in nodes:
        try:
            link = li.select_one("a[href]")
            href = (link.get("href") or "").strip() if link else ""
            title = (link.get("title") or link.get_text(" ", strip=True)).strip() if link else ""
            # The official template places the date either beside or inside the anchor.
            date_span = next((span for span in li.select("span") if parse_date(span.get_text(strip=True))), None)
            pub_at = parse_date(date_span.get_text(strip=True)) if date_span else None
            if date_span is not None and date_span in link.descendants and not link.get("title"):
                title = title.replace(date_span.get_text(strip=True), "").strip()
            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录字段缺失或日期无效: {urljoin(TARGET_URL, href)}")
                continue
            dates.append(pub_at)
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
                             "content": _extract_content(session, url, metrics), "selected": False,
                             "category": CATEGORY, "source": SOURCE_NAME})
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {TARGET_URL} - {exc}")
    count_match = re.search(r"totalRecord\s*:\s*(\d+)", response.text)
    if count_match and int(count_match.group(1)) > len(nodes):
        covered = (len(dates) == len(nodes) and dates == sorted(dates, reverse=True)
                   and min(dates) < target_from)
        if not covered:
            # The advertised getMessage.do endpoint currently returns HTTP 404.
            # Preserve the available records and make historical-window incompleteness visible.
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 日期窗口超出静态列表，官网后续分页接口待验证: {TARGET_URL}")
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
