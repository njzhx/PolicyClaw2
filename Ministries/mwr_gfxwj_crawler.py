"""水利部规范性文件爬虫。"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from crawler_core import CrawlerMetrics, CrawlerRunResult, get_crawl_date_window, is_target_date, parse_date
from db_utils import save_to_policy

TARGET_URL = "http://www.mwr.gov.cn/zw/zcfg/gfxwj/"
SOURCE_NAME = "水利部_规范性文件"
CATEGORY = "中央部委"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one(".gknb_content, #zoom, .article-content")
        if element:
            for node in element.select("script, style, noscript"):
                node.decompose()
            text = element.get_text("\n", strip=True)
            if text:
                return text
        metrics.errors.append(f"[CONTENT_MISSING] 正文选择器未命中或正文为空: {article_url}")
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
    return ""


def scrape_data():
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        container = soup.select_one("div.slnewscon")
        nodes = container.select("ul li") if container else []
        metrics.raw_item_count = len(nodes)
        if not nodes:
            metrics.errors.append(f"列表页解析为空: {TARGET_URL}")
        for node in nodes:
            try:
                link = node.select_one("a[href]")
                title = ((link.get("title") or link.get_text(" ", strip=True)) if link else "").strip()
                href = (link.get("href") or "").strip() if link else ""
                match = re.search(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", node.get_text(" ", strip=True))
                pub_at = parse_date(match.group(0)) if match else None
                if not pub_at:
                    path_match = re.search(r"/(\d{4})(\d{2})(\d{2})/", href)
                    pub_at = parse_date("-".join(path_match.groups())) if path_match else None
                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue
                article_url = urljoin(TARGET_URL, href)
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
                metrics.errors.append(f"列表记录解析失败: {exc}")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
    finally:
        session.close()
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not item.get("content") for item in policies)
    return policies, latest_items[:5], metrics


def run():
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(items=processed_items, latest_items=latest_items, metrics=metrics,
                            api_push_result=api_push_result)


if __name__ == "__main__":
    run()
