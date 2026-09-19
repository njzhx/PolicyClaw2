"""
连云港市东海县_部门发文爬虫
目标栏目：http://www.jsdh.gov.cn/dhxzf/gfxwj/gfxwj.html
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


TARGET_URL = "http://www.jsdh.gov.cn/dhxzf/gfxwj/gfxwj.html"
SOURCE_NAME = "连云港市东海县_部门发文"
CATEGORY = "连云港_东海县"

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
        for selector in ('#wznr03', '.TRS_UEDITOR', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '#zoom', '.xlxlcont', '.Custom_UnionStyle', '.article', '.content'):
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
    session.proxies = {"http": None, "https": None}

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        for li in soup.select("li"):
            link = li.select_one("a")
            if not link or not link.get("href"):
                continue
            href = (link.get("href") or "").strip()
            if not href or "javascript" in href or href == "#":
                continue
            title = link.get("title") or link.get_text(" ", strip=True)
            title = title.strip()
            if not title or len(title) < 5:
                continue
            # 日期在 li 末尾的 span（float:right）
            date_span = li.find("span", style=re.compile(r"float:\s*right"))
            if not date_span:
                # 备选：li 中最后的 span 含日期
                for sp in li.select("span"):
                    if re.search(r"\d{4}-\d{2}-\d{2}", sp.get_text()):
                        date_span = sp
                        break
            pub_at = parse_date(date_span.get_text(strip=True)) if date_span else None
            if not pub_at:
                continue
            article_url = urljoin(TARGET_URL, href)
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
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.raw_item_count = metrics.valid_item_count + metrics.invalid_item_count
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
