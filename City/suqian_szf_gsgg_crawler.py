import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://www.suqian.gov.cn/cnsq/sygsgg/list.shtml"
SOURCE_NAME = "宿迁市人民政府_公示公告"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.suqian.gov.cn/",
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


def _parse_list_page(html, metrics):
    """解析列表页HTML"""
    soup = BeautifulSoup(html, "html.parser")
    list_ul = soup.select_one("ul.list")
    if not list_ul:
        metrics.errors.append("未找到列表容器 ul.list")
        return []

    items = list_ul.select("li")
    metrics.raw_item_count = len(items)

    parsed_items = []
    for li in items:
        link = li.select_one("a")
        if not link:
            metrics.invalid_item_count += 1
            continue

        title = link.get_text(strip=True)
        href = link.get("href", "").strip()
        if not title or not href:
            metrics.invalid_item_count += 1
            continue

        # 日期在span中
        span = li.select_one("span")
        raw_date = span.get_text(strip=True) if span else ""
        pub_at = parse_date(raw_date)
        if not pub_at:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"无法解析日期: {title[:30]}... raw_date={raw_date}")
            continue

        # 处理相对链接
        article_url = urljoin("https://www.suqian.gov.cn", href)
        metrics.valid_item_count += 1

        parsed_items.append({
            "title": title,
            "url": article_url,
            "pub_at": pub_at,
        })

    return parsed_items


def _extract_content(session_or_url, article_url, metrics):
    """提取详情页正文和日期"""
    try:
        if hasattr(session_or_url, "get"):
            html = session_or_url.get(article_url, headers=HEADERS, timeout=DETAIL_TIMEOUT).text
        else:
            html = _fetch_with_retry(article_url, timeout=DETAIL_TIMEOUT)

        soup = BeautifulSoup(html, "html.parser")

        # 正文在 id=zoomcon
        content_elem = soup.select_one("#zoomcon")
        content = ""
        if content_elem:
            content = content_elem.get_text("\n", strip=True)

        # 详情页日期在 .left 容器中
        detail_date = None
        left_container = soup.select_one(".left")
        if left_container:
            date_match = re.search(r"发布日期[：:]\s*(\d{4}-\d{2}-\d{2})", left_container.get_text())
            if date_match:
                detail_date = date_match.group(1)

        return content, detail_date
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return "", None


def _get_total_pages(html):
    """从页面HTML中提取总页数"""
    # createPageHTML('page_div',8, 1,'list','shtml',118);
    page_count_match = re.search(r"createPageHTML\s*\([^)]+,\s*(\d+)\s*,", html)
    if page_count_match:
        return int(page_count_match.group(1))
    return 1


def _get_page_url(page_index):
    """生成分页URL"""
    if page_index == 1:
        return TARGET_URL
    return TARGET_URL.replace("/list.shtml", f"/list_{page_index}.shtml")


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

        if not items and page_index == 1:
            metrics.errors.append("列表页解析失败或无数据")
            break

        for item in items:
            title = item["title"]
            url = item["url"]
            pub_at = item["pub_at"]

            latest_items.append({"title": title, "pub_at": pub_at})

            if not is_target_date(pub_at, target_from, target_to):
                metrics.filtered_count += 1
                continue

            # 获取正文
            content, detail_date = _extract_content(None, url, metrics)

            # 如果列表页日期与详情页不一致，记录警告但仍使用列表页日期
            if detail_date:
                detail_pub_at = parse_date(detail_date)
                if detail_pub_at and detail_pub_at != pub_at:
                    metrics.errors.append(f"日期不一致: {title[:30]}... 列表={pub_at}, 详情={detail_pub_at}")

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
