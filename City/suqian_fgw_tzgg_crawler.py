"""
宿迁市发展和改革委员会_通知公告爬虫
目标栏目：https://fgw.suqian.gov.cn/sfgw/tzgg/list.shtml
分页机制：URL后缀 list_N.shtml (N从2开始)，总页数从 createPageHTML 函数中解析
列表选择器：li.texto
列表页日期：从li.texto文本中提取，格式如 "[2026-08-05]"
详情页选择器：div.article 或 div.wp.article-content
详情页日期：从publishtime标签或ly_div中提取
"""
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


TARGET_URL = "https://fgw.suqian.gov.cn/sfgw/tzgg/list.shtml"
SOURCE_NAME = "宿迁市发改委_通知公告"
CATEGORY = "宿迁"
BASE_URL = "https://fgw.suqian.gov.cn"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_RETRIES = 3
MAX_PAGES = 100


def _fetch_with_retry(url, timeout=LIST_TIMEOUT, session=None):
    """带重试的HTTP请求"""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if session is None:
                session = requests.Session()
                session.trust_env = False
            response = session.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                pass
    raise last_error


def _get_total_pages(html):
    """从页面HTML中提取总页数"""
    # 格式: createPageHTML('page_div', 11, 1, 'list', 'shtml', 154);
    match = re.search(r"createPageHTML\s*\([^)]+,\s*(\d+)\s*,\s*\d+\s*,\s*[^)]+\)", html)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return 1


def _get_page_url(page_index):
    """生成分页URL"""
    if page_index == 1:
        return TARGET_URL
    return TARGET_URL.replace("/list.shtml", f"/list_{page_index}.shtml")


def _parse_list_page(html, metrics):
    """解析列表页HTML"""
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select("li.texto")

    if not nodes:
        metrics.errors.append("列表页未找到 li.texto 元素")
        return []

    metrics.raw_item_count = len(nodes)
    items = []

    for node in nodes:
        try:
            link = node.select_one("a")
            if not link:
                metrics.invalid_item_count += 1
                continue

            title = link.get_text(" ", strip=True)
            href = link.get("href", "").strip()

            if not title or not href:
                metrics.invalid_item_count += 1
                continue

            # 从li文本中提取日期，格式如 "[2026-08-05]"
            text = node.get_text(" ", strip=True)
            date_match = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", text)
            raw_date = date_match.group(1) if date_match else ""

            pub_at = parse_date(raw_date) if raw_date else None

            if not pub_at:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                continue

            article_url = urljoin(BASE_URL, href)

            metrics.valid_item_count += 1
            items.append(
                {
                    "title": title,
                    "url": article_url,
                    "pub_at": pub_at,
                }
            )
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")

    return items


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = _fetch_with_retry(article_url, timeout=DETAIL_TIMEOUT, session=session)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 移除脚本和样式
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 尝试多个内容选择器
        content_elem = (
            soup.select_one("div.article")
            or soup.select_one("div.wp.article-content")
            or soup.select_one("div.contentMain")
        )

        if not content_elem:
            return ""

        # 移除底部功能按钮
        for tag in content_elem.select(".arc-ewm, .dy, .clearfix"):
            if tag.get_text(strip=True) in ("", "【TOP】【打印页面】【关闭页面】"):
                tag.decompose()

        text = content_elem.get_text("\n", strip=True)

        # 清理尾部噪声
        empty_labels = ("附件下载：", "附件下载:", "视频：", "视频:")
        lines = text.split("\n")
        while lines:
            last = lines[-1].strip()
            if last in empty_labels or not last:
                lines.pop()
            else:
                break
        text = "\n".join(lines)

        return text

    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取通知公告数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    page_index = 1
    total_pages = 1
    first_page_parsed = False

    try:
        while page_index <= MAX_PAGES:
            page_url = _get_page_url(page_index)

            try:
                response = _fetch_with_retry(page_url, timeout=LIST_TIMEOUT, session=session)
                response.encoding = response.apparent_encoding or "utf-8"
                html = response.text
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            # 第一页解析总页数
            if not first_page_parsed:
                total_pages = _get_total_pages(html)
                first_page_parsed = True

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

                content = _extract_content(session, url, metrics)

                policies.append(
                    {
                        "title": title,
                        "url": url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )

            # 检查最旧日期是否已早于目标窗口
            if items:
                oldest_date = items[-1]["pub_at"]
                if oldest_date < target_from:
                    break

            # 检查是否需要继续翻页
            if page_index >= total_pages:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"爬虫执行异常: {exc}")

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
