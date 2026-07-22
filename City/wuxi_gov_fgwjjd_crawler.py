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

TARGET_URL = "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市人民政府_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}

# 新 div 中 3 个子栏目的"更多"页面配置
# format: list 解析方式
#   "table"   -> <tbody id="doclist"> + <h4><a>，日期从 URL 路径提取
#   "ul"      -> <ul class="pd20 clearfix"> + <li><a>...<span>YYYY-MM-DD</span>
SUBSECTIONS = [
    ("地方性法规", "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/dfxfg/index.shtml", "table"),
    ("党委文件",   "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/dwwj/index.shtml",   "ul"),
    ("政府文件",   "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/zfwj/index.shtml",   "ul"),
]


def _date_from_url(href):
    """从 URL 路径 /doc/YYYY/MM/DD/... 中提取日期字符串。"""
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', href)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _fetch_with_retry(url, max_retries=3, timeout=30):
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            if attempt == max_retries:
                raise


# ── 解析器：table tbody 列表（地方性法规） ─────────────────────────────────────

class _TableListParser(HTMLParser):
    """解析 <tbody id="doclist"> 中的 <tr>，提取 <h4> 内的标题链接，从 URL 路径提取日期。"""

    def __init__(self):
        super().__init__()
        self.records = []
        self._in_tbody = False
        self._in_tr = False
        self._in_h4 = False
        self._in_a = False
        self._href = self._title = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "tbody" and d.get("id") == "doclist":
            self._in_tbody = True
        elif self._in_tbody and tag == "tr":
            self._in_tr = True
            self._href = self._title = None
        elif self._in_tr and tag == "h4":
            self._in_h4 = True
        elif self._in_h4 and tag == "a":
            self._in_a = True
            self._href = d.get("href", "").strip()

    def handle_data(self, data):
        if self._in_a and self._title is None:
            t = data.strip()
            if t:
                self._title = t

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
        elif tag == "h4" and self._in_h4:
            self._in_h4 = False
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._href and self._title:
                self.records.append((self._title, self._href))
        elif tag == "tbody" and self._in_tbody:
            self._in_tbody = False


# ── 解析器：标准 ul>li 列表（党委文件、政府文件） ─────────────────────────────

class _UlListParser(HTMLParser):
    """解析 <ul class="pd20 clearfix"> 中的 <li>，提取标题、链接、内嵌日期。"""

    def __init__(self):
        super().__init__()
        self.records = []
        self._in_ul = False
        self._in_li = False
        self._in_a = False
        self._href = self._title = self._date = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "ul" and d.get("class") == "pd20 clearfix":
            self._in_ul = True
            self._href = self._title = self._date = None
        elif self._in_ul and tag == "li":
            self._in_li = True
            self._href = self._title = self._date = None
        elif self._in_li and tag == "a":
            self._in_a = True
            self._href = d.get("href", "").strip()

    def handle_data(self, data):
        if self._in_a and self._title is None:
            t = data.strip()
            if t:
                self._title = t
        elif self._in_li and not self._in_a:
            t = data.strip()
            if len(t) == 10 and t[4] == "-" and t[7] == "-":
                self._date = t

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
        elif tag == "li" and self._in_li:
            self._in_li = False
            if self._href and self._title and self._date:
                self.records.append((self._title, self._href, self._date))
        elif tag == "ul" and self._in_ul:
            self._in_ul = False


# ── 解析器：正文 ───────────────────────────────────────────────────────────────

class _ContentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._depth = 0
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        if not self._capturing:
            attrs_dict = dict(attrs)
            cls = attrs_dict.get("class", "")
            iid = attrs_dict.get("id", "")
            if tag == "div":
                if iid == "Zoom":
                    self._capturing = True
                    self._depth = 1
                    return
                if "TRS_UEDITOR" in cls or "TRS_EDITOR" in cls:
                    self._capturing = True
                    self._depth = 1
                    return
                if cls in ("con", "content", "article", "wenZhang"):
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

    for sub_name, first_page_url, fmt in SUBSECTIONS:
        page_index = 0

        while True:
            if page_index == 0:
                page_url = first_page_url
            else:
                # 分页 URL 格式：与首页同目录，index_N.shtml
                base_dir = first_page_url.rsplit("/", 1)[0]
                page_url = f"{base_dir}/index_{page_index}.shtml"

            try:
                html = _fetch_with_retry(page_url)
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败 [{sub_name} 第{page_index + 1}页]: {exc}")
                break

            if fmt == "table":
                parser = _TableListParser()
            else:
                parser = _UlListParser()

            parser.feed(html)
            nodes = parser.records

            if page_index == 0:
                metrics.raw_item_count += len(nodes)

            if not nodes:
                break

            page_raw_count = len(nodes)
            oldest_date_on_page = None

            for node in nodes:
                try:
                    if fmt == "table":
                        title, href = node
                        raw_date = _date_from_url(href) or ""
                    else:
                        title, href, raw_date = node

                    pub_at = parse_date(raw_date) if raw_date else None

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析日期: {title[:30]}...")
                        continue

                    article_url = urljoin("https://www.wuxi.gov.cn/", href)

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

            # table 格式：基于最旧日期提前终止翻页；ul 格式不翻页
            if fmt == "table":
                if oldest_date_on_page and oldest_date_on_page < target_from:
                    break
                if page_raw_count < 20:
                    break
            else:
                # ul 格式（党委文件、政府文件）：无分页，只抓第一页
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
