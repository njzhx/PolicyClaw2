import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy

TARGET_URL = "https://www.wuxi.gov.cn/zfxxgk/szfxxgkml/fgwjjjd/zfwj/index.shtml"
SOURCE_NAME = "无锡市人民政府_政府文件"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": TARGET_URL,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# 各子栏目配置: (名称, chanid, siteId)
SUBSECTIONS = [
    ("政府规章",        59433, 168),
    ("行政规范性文件",   59434, 168),
    ("市政府文件",       59435, 168),
    ("市政府办公室文件", 59436, 168),
    ("部门文件",        59437, 168),
    ("文件修改废止",    59438, 168),
    ("政策解读",        59439, 168),
    ("图解政策",        59440, 168),
    ("视频解读",        59441, 168),
    ("一问一答",        59442, 168),
    ("媒体评论",        59443, 168),
    ("现代产业政策",    59444, 168),
]

# 同一域名（避免不同站点数据混入）
_ALLOWED_HOST = "www.wuxi.gov.cn"


def _post_with_retry(url, payload, max_retries=3, timeout=30):
    """POST form-encoded 请求，最多重试 max_retries 次。"""
    import json

    for attempt in range(1, max_retries + 1):
        try:
            data = urlencode(payload).encode("utf-8")
            req = Request(url, data=data, headers=HEADERS, method="POST")
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == max_retries:
                raise


def _fetch(url, timeout=30):
    req = Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _date_from_url(href):
    """从 URL 路径 /doc/YYYY/MM/DD/XXXXXXXX.shtml 中提取日期字符串。"""
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", href)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


class _ContentParser(HTMLParser):
    """提取详情页正文，优先找 TRS_UEDITOR 类 div，其次找其他正文容器。"""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._depth = 0
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        if not self._capturing:
            attrs_dict = dict(attrs)
            classes = attrs_dict.get("class", "")
            if tag == "div":
                if "TRS_UEDITOR" in classes or "ueditor" in classes.lower():
                    self._capturing = True
                    self._depth = 1
                    return
                if classes in ("con", "content", "article") or "TRS_EDITOR" in classes:
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
        html = _fetch(article_url, timeout=15)
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
    api_url = "https://www.wuxi.gov.cn/intertidwebapp/fzb/titleCx"

    for sub_name, chanid, siteId in SUBSECTIONS:
        page_index = 1

        while True:
            try:
                payload = {
                    "parent_id": str(chanid),
                    "KeyWord": "",
                    "siteId": siteId,
                    "pageIndex": page_index,
                    "pageSize": 20,
                }
                data = _post_with_retry(api_url, payload)
            except Exception as exc:
                metrics.errors.append(
                    f"API请求失败 [{sub_name} 第{page_index}页]: {exc}"
                )
                break

            records = data.get("list") or []
            page_count = data.get("pageCount", 0)
            total_count = data.get("totalCount", 0)

            if page_index == 1:
                metrics.raw_item_count += total_count

            if not records:
                break

            for record in records:
                try:
                    title = (record.get("title") or "").strip()
                    href = (record.get("url") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    # 仅保留同域名链接
                    if _ALLOWED_HOST not in href and not href.startswith("/"):
                        metrics.invalid_item_count += 1
                        continue

                    # 从 URL 提取日期，备用 pubDate 字段
                    raw_date = (
                        _date_from_url(href)
                        or (record.get("pubDate") or "").strip()
                    )
                    pub_at = parse_date(raw_date) if raw_date else None

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析日期: {title[:30]}...")
                        continue

                    article_url = urljoin(TARGET_URL, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

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

            if page_index >= page_count:
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
