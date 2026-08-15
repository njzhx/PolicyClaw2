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

TARGET_URL = "https://www.suzhou.gov.cn/szsrmzf/zwgg/nav_list.shtml"
SOURCE_NAME = "苏州市人民政府_政务公告"
CATEGORY = "苏州"
MAX_PAGES = 200

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch(session, url, timeout=30):
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout, proxies={"http": None, "https": None})
            response.raise_for_status()
            response.encoding = "utf-8"
            return response
        except Exception as exc:
            last_error = exc
    raise last_error


def _build_page_url(page):
    if page == 1:
        return TARGET_URL
    return "https://www.suzhou.gov.cn/szsrmzf/zwgg/nav_list_{}.shtml".format(page)


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15, proxies={"http": None, "https": None})
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        content_elem = soup.select_one("#zoomcon UCAPCONTENT")
        if not content_elem:
            content_elem = soup.select_one("#zoomcon .UCAPCONTENT")
        if not content_elem:
            content_elem = soup.select_one("#zoomcon")
        if content_elem:
            for tag in content_elem.select("script, style, iframe, nav, .pageShare, .page_relate"):
                tag.decompose()
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
    seen_urls = set()
    session = requests.Session()
    session.trust_env = False

    page_index = 1

    while page_index <= MAX_PAGES:
        page_url = _build_page_url(page_index)

        try:
            response = _fetch(session, page_url, timeout=30)
            soup = BeautifulSoup(response.text, "html.parser")

            nodes = soup.select(".page-infolist > ul.infolist > li")
            metrics.raw_item_count += len(nodes)

            if not nodes:
                break

            oldest_date_on_page = None

            for node in nodes:
                try:
                    link = node.select_one("a")
                    if not link:
                        metrics.invalid_item_count += 1
                        continue

                    title = link.get_text(" ", strip=True)
                    href = (link.get("href") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    date_elem = node.select_one("span.time")
                    raw_date = ""
                    if date_elem:
                        raw_date = date_elem.get_text(strip=True)

                    pub_at = parse_date(raw_date)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {title[:30]} - {raw_date}")
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    content = _extract_content(session, article_url, metrics)

                    policies.append({
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    })

                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {exc}")

            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            page_index += 1

        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

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
