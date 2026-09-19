"""
苏州市相城区_政府办发文爬虫
目标栏目：http://www.szxc.gov.cn/szxcrmzf/zcwj/xcxxgkml_zcwj.shtml
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


TARGET_URL = "http://www.szxc.gov.cn/szxcrmzf/zcwj/xcxxgkml_zcwj.shtml"
SOURCE_NAME = "苏州市相城区_政府办发文"
CATEGORY = "苏州_相城区"

API_PATH = '/appsearch/xcapi/list'
LIST_PARAMS = {'pid': '5a4d1ac273b04ae3a5c1038d63ef4f7d', 'channel_id': ''}
DATE_FIELD = 'qw_xcscrq'
CHANNEL_NAME = '区政府办公室文件'

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
        for selector in ('.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.xlxlcont', '.Custom_UnionStyle', '.article', '.content', '.view-content'):
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



def _parse_items(soup):
    items = []

    # 模式1: 苏州datalist-item
    for li in soup.select("li.datalist-item"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        date_span = li.select_one("span.date")
        date_text = date_span.get_text(strip=True) if date_span else ""
        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式1b: 苏州ewb-list-node
    for li in soup.select("li.ewb-list-node"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        date_span = li.select_one("span.ewb-list-date, span.date")
        date_text = date_span.get_text(strip=True) if date_span else ""
        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式2: 南通 initData ul.list-ul li
    for ul in soup.select("#initData ul.list-ul"):
        for li in ul.select("li"):
            link = li.select_one("a")
            if not link or not link.get("href"):
                continue
            title = (link.get("title") or link.get_text(" ", strip=True)).strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            date_span = li.select_one("span")
            date_text = date_span.get_text(strip=True) if date_span else ""
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
    if items:
        return items

    # 模式3: 通用ul li (支持多种结构)
    for li in soup.select("ul li"):
        link = li.select_one("a")
        if not link or not link.get("href"):
            continue
        href = (link.get("href") or "").strip()
        if not href or "javascript" in href or href == "#":
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        if not title or len(title) < 4:
            continue

        date_text = ""
        for span in li.select("span"):
            text = span.get_text(strip=True)
            if re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", text):
                date_text = text
                break
        if not date_text:
            full_text = li.get_text()
            date_match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", full_text)
            if date_match:
                date_text = date_match.group(1)

        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})

    return items


def _get_page_count(soup):
    page_text = soup.get_text()
    total_match = re.search(r"总页数[：:]\s*(\d+)", page_text)
    if total_match:
        return int(total_match.group(1))
    total_match = re.search(r"totalRecord>(\d+)", page_text)
    if total_match:
        return (int(total_match.group(1)) + 9) // 10
    return 1


def scrape_data():
    import json
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    endpoint = urljoin(TARGET_URL, API_PATH)
    seen, pages = set(), set()
    for page in range(1, 501):
        try:
            response = session.post(endpoint, params={**LIST_PARAMS, "currentPage": page, "pageSize": 20},
                                    headers=HEADERS, timeout=30)
            response.raise_for_status()
            result = response.json()
            if isinstance(result, str):
                result = json.loads(result)
            data = result["data"]
            items = data["list"]
            total = int(data["totalPage"])
            if not isinstance(items, list):
                raise ValueError("列表结构变化")
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {endpoint} page={page} - {exc}")
            break
        if not items:
            if total >= page:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {endpoint} page={page}")
            break
        signature = tuple(str(x.get("url")) for x in items)
        if signature in pages:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {endpoint} page={page}")
            break
        pages.add(signature)
        dates = []
        for item in items:
            metrics.raw_item_count += 1
            try:
                title = str(item.get("title") or "").strip()
                href = str(item.get("url") or "").strip()
                pub_at = parse_date(item.get(DATE_FIELD))
                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录缺少标题/链接/发布日期: {endpoint} page={page}")
                    continue
                metrics.valid_item_count += 1
                dates.append(pub_at)
                if item.get("channel_name") != CHANNEL_NAME:
                    continue
                article_url = urljoin(TARGET_URL, href)
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
                metrics.errors.append(f"列表记录解析失败: {endpoint} - {exc}")
        if page >= total:
            break
        if len(latest_items) >= 5 and len(dates) == len(items) and max(dates) < target_from:
            break
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {endpoint}")
    session.close()
    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not x["content"] for x in policies)
    return policies, latest_items[:5], metrics



def _build_page_url(base_url, page):
    if page <= 1:
        return base_url
    if "?" in base_url:
        return base_url + f"&page={page}"
    if base_url.endswith(".shtml"):
        return base_url.replace(".shtml", f"_{page}.shtml")
    if base_url.endswith(".html"):
        return base_url.replace(".html", f"_{page}.html")
    return base_url


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