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

# 全部子栏目：(栏目名, 相对路径)
# 前两个（政府规章、行政规范性文件）使用 table 格式，其余使用 ul 格式
SUBSECTIONS = [
    ("政府规章",        "zfgz/index.shtml",   "table"),
    ("行政规范性文件",   "gfxwj/index.shtml",  "table"),
    ("市政府文件",       "szfwj/index.shtml",  "ul"),
    ("市政府办公室文件", "szfbgswj/index.shtml", "ul"),
    ("部门文件",        "bmgfxwj/index.shtml", "ul"),
    ("文件修改废止",    "wjxgfz/index.shtml",  "ul"),
    ("政策解读",        "zcjd/index.shtml",    "ul"),
    ("图解政策",        "tjzc/index.shtml",    "ul"),
    ("视频解读",        "spjd/index.shtml",    "ul"),
    ("一问一答",        "ywyd/index.shtml",    "ul"),
    ("媒体评论",        "mtpl/index.shtml",    "ul"),
    ("现代产业政策",    "xdcyzc/index.shtml",  "ul"),
]

_LIST_BASE = "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/zfwj/"


def _date_from_url(href):
    """从 URL 路径中提取日期字符串，支持两种格式：
    /doc/YYYY/MM/DD/...（普通页面）和
    /uploadfiles/YYYY/MM/DD/YYYYMMDD...（政府规章/行政规范性文件 PDF/DOC）
    """
    # 从路径中找连续三段数字段，前两段分别是 4 位和 2 位，第三段是 2 位日期
    # 匹配 /uploadfiles/2025/12/31/... 中的 2025/12/31
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


# ── 解析器 1：标准 ul>li 列表（带内嵌日期） ───────────────────────────────────

class _UlListParser(HTMLParser):
    """解析 <ul class="pd20 clearfix"> 中的 <li>，提取标题、链接、日期。"""

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


# ── 解析器 2：table tbody 列表（无内嵌日期，从 URL 提取） ─────────────────────

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
                self.records.append((self._title, self._href, None))
        elif tag == "tbody" and self._in_tbody:
            self._in_tbody = False


# ── 解析器 3：正文 ───────────────────────────────────────────────────────────

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

    for sub_name, sub_path, fmt in SUBSECTIONS:
        page_index = 0

        while True:
            if page_index == 0:
                page_url = _LIST_BASE + sub_path
            else:
                page_url = _LIST_BASE + sub_path.rsplit("/", 1)[0] + f"/index_{page_index}.shtml"

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
                title, href, raw_date = node
                try:
                    # table 格式：无内嵌日期，从 URL 提取
                    if fmt == "table":
                        raw_date = _date_from_url(href) or ""
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

                    # PDF/DOC 等二进制文件无法解析为 HTML，跳过正文
                    if article_url.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")):
                        content = ""
                    else:
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

            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
            if page_raw_count < 20:
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
