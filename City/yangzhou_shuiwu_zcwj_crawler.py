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


TARGET_URL = "https://jiangsu.chinatax.gov.cn/"
SOURCE_NAME = "扬州市税务局_政策法规"
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
            "div.content#zoom",
            "#zoom",
            ".bt-content",
            ".article-content",
            ".TRS_Editor",
            "#barrierfree_container",
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
    session = requests.Session()
    seen_urls = set()

    page_index = 1

    while True:
        dataproxy_url = (
            f"https://jiangsu.chinatax.gov.cn/module/web/jpage/dataproxy.jsp"
            f"?page={page_index}&webid=18&path=/&columnid=20858"
            f"&unitid=154012&permissiontype=0"
        )

        try:
            resp = _fetch(dataproxy_url, timeout=30)
        except Exception as exc:
            metrics.errors.append(f"dataproxy请求失败 [第{page_index}页]: {exc}")
            break

        soup = BeautifulSoup(resp.content, "xml_parser")
        if not soup:
            soup = BeautifulSoup(resp.content, "html.parser")

        records = soup.find_all("record")
        if not records and page_index == 1:
            metrics.errors.append("dataproxy返回无record节点")
            break
        if not records:
            break

        oldest_date = None

        for record in records:
            try:
                cdata = record.string
                if not cdata:
                    cdata = record.get_text()
                if not cdata:
                    metrics.invalid_item_count += 1
                    continue

                li_soup = BeautifulSoup(cdata, "html.parser")
                li_tag = li_soup.find("li")
                if not li_tag:
                    metrics.invalid_item_count += 1
                    continue

                metrics.raw_item_count += 1

                a_tag = li_tag.find("a")
                if not a_tag:
                    metrics.invalid_item_count += 1
                    continue

                title = a_tag.get("title", "").strip() or a_tag.get_text(strip=True)
                href = (a_tag.get("href") or "").strip()

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin("https://jiangsu.chinatax.gov.cn", href)

                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                pub_at = None
                for tag_name in ["b", "span"]:
                    for tag in li_tag.find_all(tag_name):
                        date_match = re.search(
                            r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
                            tag.get_text(strip=True),
                        )
                        if date_match:
                            try:
                                pub_at = parse_date(
                                    f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                                )
                                break
                            except ValueError:
                                pass
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
                metrics.errors.append(f"record解析失败: {exc}")

        if oldest_date and oldest_date < target_from:
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