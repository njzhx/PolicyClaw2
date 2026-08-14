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


TARGET_URL = "http://js.cma.gov.cn/dsjwz/yzs/njszfxxgk/sjfdzdgknr/zcfg_01/"
SUB_LIST_URLS = [
    "http://js.cma.gov.cn/dsjwz/yzs/njszfxxgk/sjfdzdgknr/zcfg_01/sjzcwj/",
    "http://js.cma.gov.cn/dsjwz/yzs/njszfxxgk/sjfdzdgknr/zcfg_01/sjzcjd/",
]
SOURCE_NAME = "扬州市气象局_政策法规"
CATEGORY = "扬州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}


def _fetch(url, timeout=30):
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp
        except Exception as exc:
            if attempt == 2:
                raise
    return None


def _extract_content(article_url, metrics):
    try:
        resp = _fetch(article_url, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        for sel in [
            "div.content",
            "#zoom",
            ".TRS_Editor",
            ".article-content",
            "td.content",
            ".main-content",
        ]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.find_all(["script", "style"]):
                    tag.decompose()
                text = elem.get_text("\n", strip=True)
                if text:
                    return text
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页失败: {article_url} - {exc}")
        return ""


def _parse_list_page(html, base_url, metrics, seen_urls):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    ul = soup.select_one("ul.mesgopen2.list") or soup.select_one("ul.list")
    if not ul:
        return items

    nodes = ul.find_all("li", class_="list-item") or ul.find_all("li")
    metrics.raw_item_count += len(nodes)

    for node in nodes:
        try:
            link = node.find("a")
            if not link:
                metrics.invalid_item_count += 1
                continue

            title = link.get_text(strip=True)
            href = (link.get("href") or "").strip()
            if not title or not href:
                metrics.invalid_item_count += 1
                continue

            article_url = urljoin(base_url, href)

            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)

            pub_at = None
            font = node.select_one("font.date")
            if font:
                pub_at = parse_date(font.get_text(strip=True))
            if not pub_at:
                for span in node.find_all("span"):
                    pub_at = parse_date(span.get_text(strip=True))
                    if pub_at:
                        break
            if not pub_at:
                date_match = re.search(
                    r"(\d{4})/(\d{1,2})/(\d{1,2})", href
                )
                if date_match:
                    pub_at = parse_date(
                        f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                    )

            if not pub_at:
                metrics.invalid_item_count += 1
                continue

            metrics.valid_item_count += 1
            items.append({
                "title": title,
                "url": article_url,
                "pub_at": pub_at,
            })
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表解析失败: {exc}")

    return items


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()

    for sub_url in SUB_LIST_URLS:
        try:
            resp = _fetch(sub_url, timeout=30)
            items = _parse_list_page(resp.text, sub_url, metrics, seen_urls)

            for item in items:
                latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

                if not is_target_date(item["pub_at"], target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                content = _extract_content(item["url"], metrics)
                policies.append(
                    {
                        "title": item["title"],
                        "url": item["url"],
                        "pub_at": item["pub_at"],
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )
        except Exception as exc:
            metrics.errors.append(f"子栏目抓取失败 {sub_url}: {exc}")

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