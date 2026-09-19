"""
南通市启东市_政府规范性文件库爬虫
目标栏目：http://www.qidong.gov.cn/qdsrmzf/yx/yx.html
说明：通过 /truecms/messageController/getMessageByCondition.do 接口获取数据
"""
import re
from datetime import datetime
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


TARGET_URL = "http://www.qidong.gov.cn/qdsrmzf/yx/yx.html"
SOURCE_NAME = "南通市启东市_政府规范性文件库"
CATEGORY = "南通_启东市"
LMXX = "yx"
API_URL = "http://www.qidong.gov.cn/truecms/messageController/getMessageByCondition.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_lmid(session, metrics):
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        html = response.text
        m = re.search(r"lmid.{0,3}?([a-f0-9\-]{36})", html)
        if m:
            return m.group(1)
        metrics.errors.append("无法从页面提取lmid")
    except Exception as exc:
        metrics.errors.append(f"提取lmid失败: {exc}")
    return None


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('.TRS_UEDITOR', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '#zoom', '.Custom_UnionStyle', '.content'):
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

    lmid = _extract_lmid(session, metrics)
    if not lmid:
        return policies, latest_items[:5], metrics

    api_headers = {
        **HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": TARGET_URL,
    }

    page = 1
    max_pages = 100
    while page <= max_pages:
        try:
            r = session.post(
                API_URL,
                data={
                    "lmxx": LMXX,
                    "lmid": lmid,
                    "isGetChild": "1",
                    "pagenum": str(page),
                    "pagesize": "15",
                    "cms_like_document_number": "",
                    "cms_like_msg_title": "",
                },
                headers=api_headers,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("msgList") or []
            if not items:
                break

            oldest_on_page = None
            for item in items:
                title = (item.get("msgtitle") or "").strip()
                htmlpath = (item.get("htmlpath") or "").strip()
                if not title or not htmlpath:
                    metrics.invalid_item_count += 1
                    continue

                pub_at = None
                fbrq = item.get("fbrq")
                if isinstance(fbrq, dict) and fbrq.get("time"):
                    pub_at = datetime.fromtimestamp(fbrq["time"] / 1000).date()
                elif isinstance(fbrq, str):
                    pub_at = parse_date(fbrq)
                if not pub_at:
                    caption = item.get("caption") or ""
                    m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", caption)
                    if m:
                        pub_at = parse_date(m.group(1))
                if not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                if oldest_on_page is None or pub_at < oldest_on_page:
                    oldest_on_page = pub_at
                article_url = urljoin(TARGET_URL, htmlpath)
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

            total = data.get("totalCount") or 0
            page_size = data.get("pageSize") or 15
            total_pages = (total + page_size - 1) // page_size if total else 0
            if page >= total_pages:
                break
            if oldest_on_page and oldest_on_page < target_from:
                break
            page += 1
        except Exception as exc:
            metrics.errors.append(f"列表页{page}抓取失败: {exc}")
            break
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限，日期窗口未覆盖: {TARGET_URL}")

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
