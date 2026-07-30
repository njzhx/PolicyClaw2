"""
无锡市审计局_法规文件及解读爬虫
目标栏目：https://sjj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml

发布日期来源：
  - 从列表页链接后的日期文本提取
  - 日期格式：严格匹配 YYYY-MM-DD
  - 日期缺失时跳过该记录，不使用默认日期
"""
import re
from datetime import datetime
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


TARGET_URL = "https://sjj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市审计局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "sjj.wuxi.gov.cn"


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


def _extract_pub_date_from_list(text):
    """
    从列表文本中提取发布日期
    返回: raw_date_str 或 None
    """
    if not text:
        return None
    # 匹配 YYYY-MM-DD 格式
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        raw_date = match.group(1)
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
            return raw_date
        except ValueError:
            return None
    return None


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
            ".publish-time",
            ".pub-time",
            ".time",
            "[class*='publish']",
            "[class*='pubDate']",
            "[class*='date']",
        ]

        for selector in date_selectors:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text(strip=True)
                # 提取日期部分
                date_match = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)", text)
                if date_match:
                    raw_date = date_match.group(1)
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
            soup.select_one(".content")
            or soup.select_one("#zoom")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_UEDITOR")
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


def _parse_list_page(html):
    """
    解析列表页，提取标题、链接和日期
    返回: [{"title": str, "href": str, "raw_date": str}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    records = []

    # 查找所有列表项（通常在 ul/ol 列表或特定容器中）
    # 模式1: ul > li > a + 日期文本
    list_containers = soup.select("ul, ol")
    for container in list_containers:
        items = container.select("li")
        for item in items:
            links = item.select("a")
            for link in links:
                title = link.get_text(strip=True)
                href = link.get("href", "").strip()
                if not title or not href:
                    continue
                # 跳过外链
                if "://" in href and ALLOWED_HOST not in href:
                    continue
                # 获取链接后的文本（可能是日期）
                after_link_text = ""
                for sibling in link.find_all_next(string=True):
                    if sibling.parent and sibling.parent in item.children if hasattr(sibling.parent, 'children') else True:
                        after_link_text += str(sibling)
                        if len(after_link_text) > 20:
                            break
                raw_date = _extract_pub_date_from_list(after_link_text)
                if raw_date:
                    records.append({
                        "title": title,
                        "href": href,
                        "raw_date": raw_date,
                    })
                    break  # 每个li只取第一个有效链接

    # 模式2: 直接查找带有日期的链接模式
    if not records:
        for link in soup.find_all("a"):
            title = link.get_text(strip=True)
            href = link.get("href", "").strip()
            if not title or len(title) < 5:
                continue
            if "://" in href and ALLOWED_HOST not in href:
                continue
            # 获取链接后的兄弟文本
            after_text = ""
            for sibling in link.find_all_next(string=True):
                parent = sibling.parent
                if parent and hasattr(parent, 'children'):
                    if any(sibling in list(parent.children) for sibling in link.find_all_next()):
                        after_text += str(sibling)
                        if len(after_text) > 20:
                            break
            raw_date = _extract_pub_date_from_list(after_text)
            if raw_date:
                records.append({
                    "title": title,
                    "href": href,
                    "raw_date": raw_date,
                })

    return records


def scrape_data():
    """抓取无锡市审计局法规文件及解读列表"""
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

            records = _parse_list_page(html)

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
                        metrics.errors.append(
                            f"字段缺失: title={bool(title)}, href={bool(href)}, raw_date={bool(raw_date)}"
                        )
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
            if page_raw_count < 15:
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
