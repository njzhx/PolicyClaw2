"""
无锡市数据局_法规政策与文件爬虫
目标栏目：https://bigdata.wuxi.gov.cn/zfxxgk/fdzdgknr/fgwjjjd/index.shtml

发布日期来源：
  - 优先从列表页中 <a> 标签后的第一个日期文本提取
  - 如果列表页无明确日期节点，则从详情页提取
  - 日期格式：严格匹配 YYYY-MM-DD
  - 日期缺失时跳过该记录，不使用默认日期
"""
import re
from datetime import datetime
from html.parser import HTMLParser
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


TARGET_URL = "https://bigdata.wuxi.gov.cn/zfxxgk/fdzdgknr/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市数据局_法规政策与文件"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "bigdata.wuxi.gov.cn"


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response
        except Exception as exc:
            if attempt == max_retries:
                raise
            print(f"[RETRY] 第 {attempt} 次请求失败: {url} - {exc}")


def _extract_pub_date_from_detail(session, article_url, metrics):
    """
    从详情页提取发布日期
    返回: (raw_date_str, parsed_date) 或 (None, None)
    """
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 尝试多种详情页日期选择器
        date_selectors = [
            # 标准格式：发布时间字段
            (".info-content .time", "info-content time"),
            (".article-info .time", "article-info time"),
            (".publish-time", "publish-time"),
            (".pub-time", "pub-time"),
            ("[class*='publish']", "publish class"),
            ("[class*='pubDate']", "pubDate class"),
            ("[class*='date']", "date class"),
        ]

        for selector, desc in date_selectors:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text(strip=True)
                # 提取日期部分
                date_match = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)", text)
                if date_match:
                    raw_date = date_match.group(1)
                    # 标准化
                    normalized = raw_date.replace("年", "-").replace("月", "-").replace("日", "")
                    try:
                        parsed = datetime.strptime(normalized[:10], "%Y-%m-%d").date()
                        return raw_date, parsed
                    except ValueError:
                        continue

        # 尝试从 meta 标签提取
        meta_date = soup.select_one('meta[name="publishdate"]')
        if meta_date:
            content = meta_date.get("content", "")
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content)
            if date_match:
                raw_date = date_match.group(1)
                try:
                    parsed = datetime.strptime(raw_date, "%Y-%m-%d").date()
                    return raw_date, parsed
                except ValueError:
                    pass

        return None, None

    except Exception as exc:
        metrics.errors.append(f"详情页日期提取失败: {article_url} - {exc}")
        return None, None


def _extract_content(session, article_url, metrics):
    """抓取详情页正文内容"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 尝试多个正文选择器
        content_elem = (
            soup.select_one("#zoom")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".content")
            or soup.select_one(".article-content")
            or soup.select_one(".main_content")
        )

        if content_elem:
            # 移除脚本和样式
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


class ListItemParser(HTMLParser):
    """
    解析列表页中的每个 li 元素
    提取：标题、链接、发布日期（仅从链接后的第一个明确日期）
    """

    def __init__(self):
        super().__init__()
        self.records = []
        self._in_li = False
        self._in_anchor = False
        self._after_anchor = False
        self._current_href = None
        self._current_title = None
        self._after_anchor_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "li":
            self._in_li = True
            self._current_href = None
            self._current_title = None
            self._after_anchor = False
            self._after_anchor_text = ""
        elif self._in_li and tag == "a":
            self._in_anchor = True
            self._current_href = attrs_dict.get("href", "").strip()

    def handle_data(self, data):
        if self._in_anchor and self._current_title is None:
            # 只取链接内的第一个文本
            text = data.strip()
            if text:
                self._current_title = text
        elif self._in_li and not self._in_anchor:
            # 链接后的文本（可能是日期）
            if self._after_anchor or self._current_title is not None:
                self._after_anchor = True
                self._after_anchor_text += data

    def handle_endtag(self, tag):
        if tag == "a" and self._in_anchor:
            self._in_anchor = False
            self._after_anchor = True
        elif tag == "li" and self._in_li:
            self._in_li = False
            self._after_anchor = False

            if self._current_title and self._current_href:
                # 从链接后的文本中提取第一个 YYYY-MM-DD 格式的日期
                raw_date = None
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", self._after_anchor_text)
                if date_match:
                    raw_date = date_match.group(1)
                    # 严格验证日期格式
                    try:
                        datetime.strptime(raw_date, "%Y-%m-%d")
                        self.records.append({
                            "title": self._current_title,
                            "href": self._current_href,
                            "raw_date": raw_date,
                        })
                    except ValueError:
                        # 日期格式无效，不记录
                        pass


def scrape_data():
    """抓取无锡市数据局法规政策与文件列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    seen_urls = set()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index}.shtml"

            try:
                response = _fetch_with_retry(page_url)
                html = response.text
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            # 使用 HTMLParser 解析列表
            parser = ListItemParser()
            parser.feed(html)
            records = parser.records

            if not records:
                break

            page_raw_count = len(records)
            if page_index == 0:
                metrics.raw_item_count = page_raw_count

            oldest_date_on_page = None

            for record in records:
                try:
                    title = record["title"]
                    href = record["href"]
                    raw_date = record["raw_date"]

                    if not title or not href or not raw_date:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"字段缺失: title={bool(title)}, href={bool(href)}, raw_date={bool(raw_date)}")
                        continue

                    # 过滤外链
                    if "://" in href and ALLOWED_HOST not in href:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    # 严格解析日期
                    pub_at = parse_date(raw_date)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"日期解析失败: {raw_date} - {title[:30]}...")
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
    """执行抓取并保存数据"""
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
