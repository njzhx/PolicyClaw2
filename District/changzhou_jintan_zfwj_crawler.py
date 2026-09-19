"""
常州市金坛区_政府发文爬虫
目标栏目：https://www.jintan.gov.cn/class/IKJJACAL?furl=qzfwj&t=1
"""
import re
from urllib.parse import urljoin, parse_qs, urlparse, urlencode, urlunparse

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


TARGET_URL = "https://www.jintan.gov.cn/class/IKJJACAL?furl=qzfwj&t=1"
SOURCE_NAME = "常州市金坛区_政府发文"
CATEGORY = "常州_金坛区"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_iframe_src(session, metrics):
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text

        pattern = r"furl\.indexOf\('qzfwj'\)\s*!=\s*-1[^}]*?catid=(\d+)"
        catid_match = re.search(pattern, html)
        if catid_match:
            catid = catid_match.group(1)
            return urljoin(TARGET_URL, f"/content/xxgk/index?catid={catid}")

        soup = BeautifulSoup(response.content, "html.parser")
        iframe = soup.select_one("#FrameMoreInfo")
        if iframe and iframe.get("src"):
            src = iframe["src"]
            if not src.startswith("http"):
                src = urljoin(TARGET_URL, src)
            return src
    except Exception as exc:
        metrics.errors.append(f"提取iframe src失败: {exc}")
    return None


def _build_page_url(base_url, page):
    if page <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _parse_items(soup):
    items = []
    xxgk = soup.select_one(".xxgkList")
    if not xxgk:
        return items
    for p in xxgk.select("p"):
        link = p.select_one("a")
        if not link or not link.get("href"):
            continue
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        href = (link.get("href") or "").strip()
        if not title or not href:
            continue
        time_span = p.select_one("span.time")
        date_text = time_span.get_text(strip=True) if time_span else ""
        pub_at = parse_date(date_text) if date_text else None
        if pub_at:
            items.append({"title": title, "url": href, "pub_at": pub_at})
    return items


def _get_page_count(soup):
    page_text = soup.get_text()
    total_match = re.search(r"总页数[：:]\s*(\d+)", page_text)
    if total_match:
        return int(total_match.group(1))
    page_links = soup.select(".page a, .pagination a, #page a")
    if page_links:
        nums = []
        for a in page_links:
            try:
                nums.append(int(a.get_text(strip=True)))
            except ValueError:
                pass
        if nums:
            return max(nums)
    return 1


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('td.NewsContent', '.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.Custom_UnionStyle', '.article', '.content'):
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

    iframe_src = _extract_iframe_src(session, metrics)
    if not iframe_src:
        return policies, latest_items[:5], metrics

    iframe_headers = {**HEADERS, "Referer": TARGET_URL}
    all_items = []
    page = 1
    while page <= 100:
        page_url = _build_page_url(iframe_src, page)
        try:
            response = session.get(page_url, headers=iframe_headers, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.content, "html.parser")
            items = _parse_items(soup)
            if not items:
                break
            for item in items:
                item["url"] = urljoin(page_url, item["url"])
            all_items.extend(items)
            total_pages = _get_page_count(soup)
            if page >= total_pages:
                break
            page += 1
        except Exception as exc:
            metrics.errors.append(f"列表页{page}抓取失败: {exc}")
            break
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限，日期窗口未覆盖: {TARGET_URL}")

    metrics.raw_item_count = len(all_items)

    for item in all_items:
        metrics.valid_item_count += 1
        latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

        if not is_target_date(item["pub_at"], target_from, target_to):
            metrics.filtered_count += 1
            continue

        content = _extract_content(session, item["url"], metrics)
        policies.append({
            "title": item["title"],
            "url": item["url"],
            "pub_at": item["pub_at"],
            "content": content,
            "selected": False,
            "category": CATEGORY,
            "source": SOURCE_NAME,
        })

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
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