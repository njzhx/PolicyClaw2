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


TARGET_URL = "https://dpc.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市发改委_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}

MAX_RETRIES = 3
LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15


def _fetch_with_retry(url, max_retries=MAX_RETRIES, timeout=LIST_TIMEOUT):
    """带重试的HTTP请求，最多重试max_retries次"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                pass
    raise last_error


class _ListParser(HTMLParser):
    """解析列表页HTML，提取标题、链接和日期"""

    def __init__(self):
        super().__init__()
        self.items = []
        self._in_list = False
        self._in_li = False
        self._in_link = False
        self._in_span = False
        self._current_title = ""
        self._current_href = ""
        self._current_date = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        ul_id = attrs_dict.get("id", "")

        # 使用 id="doclist" 来识别列表
        if tag == "ul" and ul_id == "doclist":
            self._in_list = True
        elif tag == "li" and self._in_list:
            self._in_li = True
            self._current_title = ""
            self._current_href = ""
            self._current_date = ""
        elif tag == "a" and self._in_li:
            self._in_link = True
            self._current_href = attrs_dict.get("href", "")
        elif tag == "span" and self._in_li:
            self._in_span = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_link = False
        elif tag == "span":
            self._in_span = False
        elif tag == "li" and self._in_li:
            self._in_li = False
            # 保存当前条目
            if self._current_title and self._current_href:
                self.items.append({
                    "title": self._current_title.strip(),
                    "href": self._current_href.strip(),
                    "date": self._current_date.strip(),
                })
        elif tag == "ul" and self._in_list:
            self._in_list = False

    def handle_data(self, data):
        if self._in_link and not self._in_span:
            self._current_title += data
        elif self._in_span:
            self._current_date += data


def _parse_list_page(html, metrics):
    """解析列表页HTML"""
    parser = _ListParser()
    try:
        parser.feed(html)
        items = parser.items
        metrics.raw_item_count = len(items)

        parsed_items = []
        for item in items:
            title = item["title"]
            href = item["href"]
            raw_date = item["date"]

            if not title or not href:
                metrics.invalid_item_count += 1
                continue

            pub_at = parse_date(raw_date)
            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"无法解析日期: {title[:30]}...")
                continue

            article_url = urljoin("https://dpc.wuxi.gov.cn", href)
            metrics.valid_item_count += 1

            parsed_items.append({
                "title": title,
                "url": article_url,
                "pub_at": pub_at,
            })

        return parsed_items

    except Exception as exc:
        metrics.errors.append(f"列表页解析失败: {exc}")
        return []


class _ContentParser(HTMLParser):
    """提取详情页正文"""

    def __init__(self):
        super().__init__()
        self._in_zoom = False
        self._depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        div_id = attrs_dict.get("id", "")

        if tag == "div" and div_id == "Zoom":
            self._in_zoom = True
            self._depth = 1
            return

        if self._in_zoom and tag in ("script", "style"):
            self._depth += 1

    def handle_endtag(self, tag):
        if self._in_zoom and tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._in_zoom = False
            return

        if self._in_zoom and tag in ("script", "style"):
            self._depth -= 1

    def handle_data(self, data):
        if self._in_zoom:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self):
        return "\n".join(self._parts)


def _extract_content(article_url, metrics):
    """提取详情页正文"""
    try:
        html = _fetch_with_retry(article_url, timeout=DETAIL_TIMEOUT)
        parser = _ContentParser()
        parser.feed(html)
        return parser.get_text()
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _get_page_url(page_index):
    """生成分页URL"""
    if page_index == 1:
        return TARGET_URL
    return TARGET_URL.replace("/index.shtml", f"/index_{page_index}.shtml")


def _get_total_pages(html):
    """从页面HTML中提取总页数"""
    page_count_match = re.search(r'"pageCount"\s*:\s*"(\d+)"', html)
    if page_count_match:
        return int(page_count_match.group(1))
    return 1


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()

    page_index = 1
    total_pages = 1

    while True:
        page_url = _get_page_url(page_index)

        try:
            html = _fetch_with_retry(page_url)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {exc}")
            break

        # 第一次获取总页数
        if page_index == 1:
            total_pages = _get_total_pages(html)

        # 解析列表
        items = _parse_list_page(html, metrics)

        if not items:
            break

        for item in items:
            title = item["title"]
            url = item["url"]
            pub_at = item["pub_at"]

            latest_items.append({"title": title, "pub_at": pub_at})

            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue

            content = _extract_content(url, metrics)
            policies.append({
                "title": title,
                "url": url,
                "pub_at": pub_at,
                "content": content,
                "selected": False,
                "category": CATEGORY,
                "source": SOURCE_NAME,
            })

        # 检查是否需要继续翻页
        if page_index >= total_pages:
            break

        # 检查最旧日期是否已经早于目标窗口
        if items:
            oldest_date = items[-1]["pub_at"]
            if oldest_date < target_from:
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
