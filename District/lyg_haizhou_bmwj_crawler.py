"""
连云港市海州区_部门发文爬虫
目标栏目：http://www.lyghz.gov.cn/lyghzqrmzf/xxgk/xxgk.html
说明：目录页提取部门子站链接，子站首页JS跳转至文件列表页
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


TARGET_URL = "http://www.lyghz.gov.cn/lyghzqrmzf/xxgk/xxgk.html"
SOURCE_NAME = "连云港市海州区_部门发文"
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
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one("#zoom")
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

        dept_links = []
        for a in soup.select("a"):
            href = a.get("href") or ""
            text = a.get_text(strip=True)
            if text and 3 < len(text) < 25 and href and "lyghz.gov.cn" in href and "hzq" in href:
                dept_links.append(href)
        dept_links = list(dict.fromkeys(dept_links))
        metrics.raw_item_count = len(dept_links)

        for dept_url in dept_links:
            try:
                r = session.get(dept_url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                m = re.search(r"location\.href\s*=\s*[\"']([^\"']+)[\"']", r.text)
                if not m:
                    continue
                list_url = urljoin(dept_url, m.group(1))

                r2 = session.get(list_url, headers=HEADERS, timeout=30)
                r2.raise_for_status()
                r2.encoding = r2.apparent_encoding or "utf-8"
                soup2 = BeautifulSoup(r2.content, "html.parser")
                for li in soup2.select("li"):
                    link = li.select_one("a")
                    if not link or not link.get("href"):
                        continue
                    href = (link.get("href") or "").strip()
                    if not href or "javascript" in href:
                        continue
                    title = link.get("title") or link.get_text(" ", strip=True)
                    title = title.strip()
                    if not title or len(title) < 5:
                        continue
                    date_span = None
                    for sp in li.select("span"):
                        if re.search(r"\d{4}-\d{2}-\d{2}", sp.get_text()):
                            date_span = sp
                            break
                    pub_at = parse_date(date_span.get_text(strip=True)) if date_span else None
                    if not pub_at:
                        continue
                    article_url = urljoin(list_url, href)
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
                metrics.errors.append(f"部门页抓取失败: {dept_url} - {exc}")
                continue
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {exc}")

    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
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