"""
连云港市灌南县_部门信息公开爬虫
目标栏目：http://xxgk.guannan.gov.cn/group/3.html
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "http://xxgk.guannan.gov.cn/group/3.html"
SOURCE_NAME = "连云港市灌南县_部门信息公开"
CATEGORY = "连云港_灌南县"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _new_session():
    session = requests.Session()
    session.trust_env = False
    retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _extract_departments(html):
    soup = BeautifulSoup(html, "html.parser")
    departments = []
    for link in soup.select('a[href^="/list/"]'):
        href = str(link.get("href") or "").strip()
        if not re.fullmatch(r"/list/\d+-0-0-0\.html", href):
            continue
        name = re.sub(r"^[·•\s]+|（\d+）\s*$", "", link.get_text(" ", strip=True))
        url = urljoin(TARGET_URL, href)
        if name and url not in [item[1] for item in departments]:
            departments.append((name, url))
    return departments


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



def scrape_data():
    import os
    import time
    from urllib.parse import urldefrag, urlsplit

    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = _new_session()
    seen = set()
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        departments = _extract_departments(response.text)
        if not departments:
            raise ValueError("[CHANNEL_MISMATCH] 官网部门目录未解析到县级部门")
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics

    for department_name, first_url in departments:
        page_url, visited = first_url, set()
        previous_oldest = None
        for page_number in range(1, 501):
            deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
            if deadline and time.time() + 35 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {page_url}")
                break
            if page_url in visited:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {page_url}")
                break
            visited.add(page_url)
            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                soup = BeautifulSoup(response.text, "html.parser")
                if page_number == 1:
                    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
                    if department_name not in page_title:
                        raise ValueError(f"[CHANNEL_MISMATCH] 部门列表归属不符: {department_name}")
                nodes = [a.find_parent("tr") for a in soup.select('td a[href^="/news/show-"]')]
                if not nodes:
                    metrics.errors.append(f"列表页抓取失败: 未解析到文章记录 {page_url}")
                    break
                metrics.raw_item_count += len(nodes)
                page_dates = []
                for node in nodes:
                    try:
                        link = node.select_one('a[href^="/news/show-"]')
                        cells = node.select("td")
                        title = str(link.get("title") or link.get_text(" ", strip=True)).strip()
                        full_title = re.search(r"(?:^|##)标题::(.*?)(?:##|$)", str(link.get("data-title") or ""))
                        if full_title:
                            title = full_title.group(1).strip()
                        url = urldefrag(urljoin(page_url, link["href"]))[0]
                        pub_at = parse_date(cells[-1].get_text(strip=True)) if len(cells) >= 5 else None
                        if not title or not pub_at:
                            metrics.invalid_item_count += 1
                            metrics.errors.append(f"列表记录标题或日期无效: {url}")
                            continue
                        page_dates.append(pub_at)
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
                        metrics.errors.append(f"列表记录解析失败: {page_url} - {exc}")
                ordered = (len(page_dates) == len(nodes)
                           and page_dates == sorted(page_dates, reverse=True)
                           and (previous_oldest is None or max(page_dates) <= previous_oldest))
                if ordered and min(page_dates) < target_from:
                    break
                previous_oldest = min(page_dates) if ordered else None
                next_link = next((a for a in soup.select('a[href]')
                                  if a.get_text(strip=True).startswith("下一页")), None)
                if next_link is None:
                    break
                following = urljoin(page_url, next_link["href"])
                expected = urlsplit(first_url).path.removesuffix(".html") + "-"
                if (urlsplit(following).hostname != urlsplit(TARGET_URL).hostname
                        or not urlsplit(following).path.startswith(expected)):
                    raise ValueError("[CHANNEL_MISMATCH] 下一页脱离已确认部门栏目")
                page_url = following
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break
        else:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {page_url}")
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
