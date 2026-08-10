from html.parser import HTMLParser
from urllib.parse import urljoin, urlunsplit, urlsplit

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


TARGET_URL = "https://www.changzhou.gov.cn/ns_class/zwgk_10_18_01"
SOURCE_NAME = "常州市人民政府_文字解读"
CATEGORY = "常州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """带重试的HTTP请求"""
    session = requests.Session()
    session.trust_env = False
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout, proxies={"http": None, "https": None})
            response.raise_for_status()
            return response
        except Exception as exc:
            if attempt == max_retries:
                raise


class _ListPageParser(HTMLParser):
    """解析列表页表格"""

    def __init__(self):
        super().__init__()
        self.records = []
        self._in_table = False
        self._in_tr = False
        self._in_td = False
        self._td_index = 0
        self._current_link = None
        self._current_title = None
        self._current_date = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_tr = True
            self._td_index = 0
            self._current_link = None
            self._current_title = None
            self._current_date = None
        elif self._in_tr and tag == "td":
            self._in_td = True
            self._td_index += 1
        elif self._in_td and tag == "a":
            self._current_link = d.get("href", "").strip()

    def handle_data(self, data):
        if self._in_td:
            data = data.strip()
            if self._td_index == 1 and data:
                self._current_title = data
            elif self._td_index == 2 and len(data) == 10 and data[4] == "-" and data[7] == "-":
                self._current_date = data

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._current_link and self._current_title and self._current_date:
                self.records.append({
                    "title": self._current_title,
                    "href": self._current_link,
                    "date": self._current_date,
                })
        elif tag == "table":
            self._in_table = False


class _DetailPageParser(HTMLParser):
    """解析详情页正文"""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._in_content = False
        self._content_depth = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get("class", "")
        iid = d.get("id", "")

        if tag == "table":
            self._in_content = True
            self._content_depth = 1
            return

        if self._in_content:
            self._content_depth += 1

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_content and len(text) >= 5:
            self._parts.append(text)

    def handle_endtag(self, tag):
        if self._in_content:
            self._content_depth -= 1
            if self._content_depth <= 0:
                self._in_content = False

    def get_text(self):
        return "\n".join(self._parts)


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = _fetch_with_retry(article_url, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.select_one("td#czfxfontzoom.NewsContent")
        if not element:
            return ""
        for node in element.select("script, style"):
            node.decompose()
        return element.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取文字解读数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()

    page_index = 1
    max_pages = 1000

    while page_index <= max_pages:
        if page_index == 1:
            page_url = TARGET_URL
        else:
            page_url = f"{TARGET_URL}/{page_index}"

        try:
            response = _fetch_with_retry(page_url)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        parser = _ListPageParser()
        parser.feed(response.text)
        nodes = parser.records

        metrics.raw_item_count += len(nodes)

        if not nodes:
            break

        oldest_date_on_page = None

        for node in nodes:
            try:
                title = node.get("title", "").strip()
                href = node.get("href", "").strip()
                raw_date = node.get("date", "").strip()

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                pub_at = parse_date(raw_date) if raw_date else None

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析日期: {title[:30]}... (原始: {raw_date})")
                    continue

                article_url = urljoin("https://www.changzhou.gov.cn", href)

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

        if len(nodes) < 20:
            break

        page_index += 1

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items[:5], metrics


def run():
    """执行爬虫"""
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
