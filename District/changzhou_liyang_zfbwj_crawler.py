"""
常州市溧阳市_政府办发文爬虫
目标栏目：https://www.liyang.gov.cn/class/OPEHQFEM
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


TARGET_URL = "https://www.liyang.gov.cn/class/OPEHQFEM"
SOURCE_NAME = "常州市溧阳市_政府办发文"
CATEGORY = "常州_溧阳市"

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

        furl_match = re.search(r'[?&]furl=([^&]+)', TARGET_URL)
        if furl_match:
            furl = furl_match.group(1)
            pattern = r"furl\.indexOf\('" + re.escape(furl) + r"'\)\s*!=\s*-1[^}]*?catid=(\d+)"
            catid_match = re.search(pattern, html)
            if catid_match:
                catid = catid_match.group(1)
                return urljoin(TARGET_URL, f"/content/xxgk/index?catid={catid}")

        soup = BeautifulSoup(response.content, "html.parser")
        iframe = soup.select_one("#FrameMoreInfo")
        if not iframe:
            iframe = soup.find("iframe", id="FrameMoreInfo")
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


def _parse_table_rows(soup):
    items = []

    # 模式1: table.gov_box3
    table = soup.select_one("table.gov_box3")
    if table:
        for row in table.select("tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            link = row.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            date_text = cells[-1].get_text(strip=True)
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
        return items

    # 模式2: #relate table
    relate = soup.select_one("#relate")
    if relate:
        for row in relate.select("tr"):
            link = row.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            date_cell = row.select_one(".datetime, td:last-child")
            date_text = date_cell.get_text(strip=True) if date_cell else ""
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
        return items

    # 模式3: table.wjk-list
    table = soup.select_one("table.wjk-list")
    if table:
        for row in table.select("tr"):
            link = row.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            cells = row.select("td")
            date_text = cells[-1].get_text(strip=True) if cells else ""
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
        return items

    # 模式4: .xxgkList p
    xxgk = soup.select_one(".xxgkList")
    if xxgk:
        for p in xxgk.select("p"):
            link = p.select_one("a")
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            time_span = p.select_one("span.time")
            date_text = time_span.get_text(strip=True) if time_span else ""
            pub_at = parse_date(date_text) if date_text else None
            if pub_at:
                items.append({"title": title, "url": href, "pub_at": pub_at})
        return items

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
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".wenZhang")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one(".pages_content")
            or soup.select_one("#zoom")
            or soup.select_one(".xlxlcont")
            or soup.select_one(".Custom_UnionStyle")
            or soup.select_one(".article")
            or soup.select_one(".content")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _fetch_page(session, url, metrics):
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return BeautifulSoup(response.content, "html.parser")
    except Exception as exc:
        metrics.errors.append(f"页面抓取失败: {url} - {exc}")
        return None


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    iframe_src = _extract_iframe_src(session, metrics)
    if iframe_src:
        iframe_headers = {**HEADERS, "Referer": TARGET_URL}
        all_items = []
        page = 1
        while page <= 10:
            page_url = _build_page_url(iframe_src, page)
            soup = _fetch_page(session, page_url, metrics)
            if not soup:
                break
            items = _parse_table_rows(soup)
            if not items:
                break
            for item in items:
                item["url"] = urljoin(page_url, item["url"])
            all_items.extend(items)
            total_pages = _get_page_count(soup)
            if page >= total_pages:
                break
            page += 1
    else:
        soup = _fetch_page(session, TARGET_URL, metrics)
        if not soup:
            return policies, latest_items[:5], metrics
        all_items = _parse_table_rows(soup)
        for item in all_items:
            item["url"] = urljoin(TARGET_URL, item["url"])

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
