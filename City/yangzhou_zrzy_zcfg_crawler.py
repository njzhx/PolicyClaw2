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


TARGET_URL = (
    "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
    "?classID=2c9082b55b60eafb015b616f566c0260&type=1"
)
SOURCE_NAME = "扬州市自然资源和规划局_政策法规"
CATEGORY = "扬州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

CLASS_ID = "2c9082b55b60eafb015b616f566c0260"


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


def _fetch_post(url, data, timeout=30):
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=HEADERS, data=data, timeout=timeout)
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
            ".main-content",
            "#article_content",
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


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()

    page_index = 1

    while True:
        try:
            if page_index == 1:
                resp = _fetch(TARGET_URL, timeout=30)
            else:
                post_url = (
                    f"https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action"
                    f"?classID={CLASS_ID}&type=1"
                )
                resp = session.post(
                    post_url,
                    headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
                    data={"cpage": page_index},
                    timeout=30,
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        tds = soup.select('td.nlist')

        if not tds and page_index == 1:
            metrics.errors.append("列表页无td.nlist节点")
            break
        if not tds:
            break

        metrics.raw_item_count += len(tds)
        oldest_date = None

        for td in tds:
            try:
                link = td.find("a")
                if not link:
                    metrics.invalid_item_count += 1
                    continue

                title = (link.get("title") or link.get_text(" ", strip=True)).strip()
                href = (link.get("href") or "").strip()
                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin("https://zrzy.jiangsu.gov.cn", href)

                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                pub_at = None
                span = td.select_one("span")
                if span:
                    pub_at = parse_date(span.get_text(strip=True))
                if not pub_at:
                    date_match = re.search(
                        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
                        td.get_text(strip=True),
                    )
                    if date_match:
                        pub_at = parse_date(
                            f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                        )

                if not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date is None or pub_at < oldest_date:
                    oldest_date = pub_at

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                content = _extract_content(article_url, metrics)
                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表解析失败: {exc}")

        if oldest_date and oldest_date < target_from:
            break
        if len(tds) < 25:
            break
        page_index += 1

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