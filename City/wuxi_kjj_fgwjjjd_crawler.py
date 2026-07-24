import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://wxkjj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市科学技术局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_ALLOWED_HOST = "wxkjj.wuxi.gov.cn"


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            if attempt == max_retries:
                raise


class _ListParser(HTMLParser):
    """解析列表页，提取 (title, href, raw_date) 三元组。

    列表结构：
    <ul id="doclist" class="pd20">
        <li>
            <a target="_blank" title="标题" href="/doc/2026/01/20/4720569.shtml">标题</a>
            <span>2026-01-20</span>
        </li>
    </ul>
    """

    def __init__(self):
        super().__init__()
        self.records = []
        self._in_li = False
        self._in_anchor = False
        self._current_href = None
        self._current_title = None
        self._current_date = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "li":
            self._in_li = True
            self._current_href = None
            self._current_title = None
            self._current_date = None
        elif self._in_li and tag == "a":
            self._in_anchor = True
            self._current_href = attrs_dict.get("href", "").strip()

    def handle_data(self, data):
        if self._in_anchor and self._current_title is None:
            text = data.strip()
            if text:
                self._current_title = text
        elif self._in_li and not self._in_anchor:
            text = data.strip()
            # 日期格式: YYYY-MM-DD
            if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                self._current_date = text

    def handle_endtag(self, tag):
        if tag == "a" and self._in_anchor:
            self._in_anchor = False
        elif tag == "li" and self._in_li:
            self._in_li = False
            if self._current_title and self._current_href and self._current_date:
                self.records.append(
                    (self._current_title, self._current_href, self._current_date)
                )


class _ContentParser(HTMLParser):
    """提取详情页正文：<div id="Zoom"> 内的段落文本。"""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._depth = 0
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if not self._capturing:
            if tag == "div" and attrs_dict.get("id") == "Zoom":
                self._capturing = True
                self._depth = 1
                return
        if self._capturing:
            self._depth += 1

    def handle_data(self, data):
        if self._capturing:
            text = data.strip()
            if text:
                self._parts.append(text)

    def handle_endtag(self, tag):
        if self._capturing:
            self._depth -= 1
            if self._depth <= 0:
                self._capturing = False

    def get_text(self):
        return "\n".join(self._parts)


def _extract_content(article_url, metrics):
    try:
        html = _fetch_with_retry(article_url, timeout=15)
        parser = _ContentParser()
        parser.feed(html)
        return parser.get_text()
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index}.shtml"

            html = _fetch_with_retry(page_url)
            parser = _ListParser()
            parser.feed(html)
            nodes = parser.records

            if not nodes:
                break

            page_raw_count = len(nodes)
            if page_index == 0:
                metrics.raw_item_count = page_raw_count

            oldest_date_on_page = None

            for title, href, raw_date in nodes:
                try:
                    pub_at = parse_date(raw_date)
                    if not title or not href or not pub_at:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    # 只保留同域名链接
                    if _ALLOWED_HOST not in article_url:
                        metrics.invalid_item_count += 1
                        continue

                    # 去重
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

                    content = _extract_content(article_url, metrics)
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

            # 分页提前终止：当前页最旧日期已早于目标窗口起始日期
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
            # 不足整页时终止
            if page_raw_count < 20:
                break
            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

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
