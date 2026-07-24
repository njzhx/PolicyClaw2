"""
无锡市人力资源和社会保障局 法规文件及解读 爬虫
目标栏目：https://hrss.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml
"""
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


TARGET_URL = "https://hrss.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市人力资源和社会保障局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


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


def _parse_list(html):
    """解析列表页 HTML，提取 (title, href, raw_date) 三元组列表。"""
    records = []

    # 找到 doclist 区域
    doclist_start = html.find('id="doclist"')
    if doclist_start == -1:
        return records

    doclist_end = html.find("</ul>", doclist_start)
    if doclist_end == -1:
        return records

    doclist_html = html[doclist_start:doclist_end + 5]

    # 提取每个 <li>...</li> 块
    li_pattern = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL)

    for li_match in li_pattern.finditer(doclist_html):
        li_content = li_match.group(1)

        # 提取 href
        href_match = re.search(r'href="([^"]+)"', li_content)
        if not href_match:
            continue
        href = href_match.group(1).strip()

        # 提取标题：<a> 标签内的文本
        a_pattern = re.compile(r"<a[^>]*>(.*?)</a>", re.DOTALL)
        a_match = a_pattern.search(li_content)
        if not a_match:
            continue
        title = a_match.group(1).strip()

        # 提取日期：<span> 标签内的文本
        span_pattern = re.compile(r"<span[^>]*>(.*?)</span>", re.DOTALL)
        span_match = span_pattern.search(li_content)
        if not span_match:
            continue
        raw_date = span_match.group(1).strip()

        if title and href and raw_date:
            records.append((title, href, raw_date))

    return records


class _ContentParser(HTMLParser):
    """提取详情页正文：div#Zoom 内的段落文本。"""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._depth = 0
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        if not self._capturing:
            if tag == "div":
                attrs_dict = dict(attrs)
                if attrs_dict.get("id") == "Zoom":
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
    """抓取详情页正文内容。"""
    try:
        html = _fetch_with_retry(article_url, timeout=15)
        parser = _ContentParser()
        parser.feed(html)
        return parser.get_text()
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取无锡市人社局法规文件及解读列表。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            # 第1页: index.shtml, 第2+页: index_N.shtml
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index}.shtml"

            html = _fetch_with_retry(page_url)
            nodes = _parse_list(html)

            if not nodes:
                break

            page_raw_count = len(nodes)
            if page_index == 0:
                metrics.raw_item_count = page_raw_count

            oldest_date_on_page = None

            for title, href, raw_date in nodes:
                try:
                    pub_at = parse_date(raw_date)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"日期解析失败: {raw_date} - {title[:30]}...")
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    # URL 去重
                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    # 记录当前页最旧日期，用于提前终止翻页
                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    # 抓取详情页正文
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
            # 不足整页时终止（无锡人社局每页20条）
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
    """执行抓取并保存数据。"""
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
