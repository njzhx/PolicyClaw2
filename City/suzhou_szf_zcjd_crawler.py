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

TARGET_URL = "https://www.suzhou.gov.cn/szsrmzf/wzjd/zdly_zcjd_wzjd.shtml"
SOURCE_NAME = "苏州市人民政府_政策解读"
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
    return "https://www.suzhou.gov.cn/szsrmzf/wzjd/zdly_zcjd_wzjd_{}.shtml".format(page)


def _extract_total_pages(soup):
    import re
    page_html = ""
    for script in soup.select("script"):
        src = script.string or ""
        if "createPageHTML" in src:
            match = re.search(r"createPageHTML\s*=\s*'([^']*)'", src)
            if match:
                page_html = match.group(1)
            break
    if not page_html:
        page_html = str(soup)

    page_nums = re.findall(r'page=([\d]+)', page_html)
    if page_nums:
        return max(int(p) for p in page_nums)
    return None


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
    total_pages_hint = None

    while page_index <= MAX_PAGES:
        page_url = _build_page_url(page_index)

        try:
            response = _fetch(session, page_url, timeout=30)
            soup = BeautifulSoup(response.text, "html.parser")

            if page_index == 1:
                total_pages_hint = _extract_total_pages(soup)

            nodes = soup.select(".zdly-list-col2 > ul.clearfix > li")
            metrics.raw_item_count += len(nodes)

            if not nodes:
                break

            oldest_date_on_page = None

            for node in nodes:
                try:
                    link = node.select_one("div.box > h4 > a")
                    if not link:
                        metrics.invalid_item_count += 1
                        continue

                    title = link.get_text(" ", strip=True)
                    href = (link.get("href") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    date_elem = node.select_one("div.box > dl.attr > dd:nth-of-type(2)")
                    raw_date = ""
                    if date_elem:
                        raw_date = date_elem.get_text(strip=True)
                        raw_date = re.sub(r"发布日期[：:]\s*", "", raw_date).strip()

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

            if total_pages_hint and page_index >= total_pages_hint:
                break

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
