"""
宿迁市人民政府_市政府办公室文件爬虫
目标栏目：https://www.suqian.gov.cn/cnsq/szfbwj/xxgk_list.shtml
列表容器：ul.listContent
列表项：ul.listContent > li
日期节点：li span
分页规则：xxgk_list_N.shtml (N从2开始)
详情页正文：div.article 或 div.xxgkcont
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


TARGET_URL = "https://www.suqian.gov.cn/cnsq/szfbwj/xxgk_list.shtml"
SOURCE_NAME = "宿迁市人民政府_市政府办公室文件"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_PAGES = 100


def _fetch_with_retry(url, session, max_retries=3, timeout=30):
    """带重试的HTTP请求"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout, proxies={"http": None, "https": None})
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                import time
                time.sleep(1)
    raise last_error


def _clean_title(title):
    """清除标题中的多余空白"""
    if not title:
        return ""
    text = re.sub(r"\s+", " ", title)
    return text.strip()


def _extract_content(session, article_url, metrics):
    """提取详情页正文"""
    try:
        response = _fetch_with_retry(article_url, session, timeout=15)
        soup = BeautifulSoup(response.content, "html.parser")

        # 移除脚本和样式
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 优先使用 div.xxgkcont（正文纯文本区域）
        content_elem = (
            soup.select_one("div.xxgkcont")
            or soup.select_one("div.article")
            or soup.select_one("div.content")
            or soup.select_one(".article-content")
        )

        if not content_elem:
            metrics.errors.append(f"未找到详情页正文容器: {article_url}")
            return ""

        # 移除噪音元素
        for noise in content_elem.select(".article-title, .article-info, .breadcrumb, nav, header, footer"):
            noise.decompose()

        # 获取纯文本，保留段落边界
        text = content_elem.get_text("\n", strip=True)

        # 清理多余空行
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取宿迁市政府办公室文件数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()

    page_index = 1

    while page_index <= MAX_PAGES:
        if page_index == 1:
            page_url = TARGET_URL
        else:
            page_url = f"https://www.suqian.gov.cn/cnsq/szfbwj/xxgk_list_{page_index}.shtml"

        try:
            response = _fetch_with_retry(page_url, session, timeout=30)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        soup = BeautifulSoup(response.content, "html.parser")

        # 找到列表容器
        list_container = soup.select_one("ul.listContent")
        if not list_container:
            # 如果是第一页且找不到列表，报告错误
            if page_index == 1:
                metrics.errors.append("未找到列表容器 ul.listContent")
            break

        # 解析列表项
        nodes = list_container.find_all("li", recursive=False)
        if not nodes:
            break

        if page_index == 1:
            metrics.raw_item_count = len(nodes)
        else:
            metrics.raw_item_count += len(nodes)

        oldest_date_on_page = None

        for node in nodes:
            try:
                # 提取标题和链接
                link = node.select_one("a")
                if not link:
                    continue

                title = _clean_title(link.get_text(" ", strip=True))
                href = link.get("href", "").strip()

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                # 提取日期
                date_elem = node.select_one("span")
                raw_date = date_elem.get_text(strip=True) if date_elem else ""

                pub_at = None
                if raw_date:
                    pub_at = parse_date(raw_date)

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {title[:30]}... (原始: {raw_date})")
                    continue

                # 转换为绝对URL
                article_url = urljoin("https://www.suqian.gov.cn/", href.lstrip("/"))

                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                # 记录最旧日期用于分页停止判断
                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                # 过滤不在目标日期范围内的记录
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 提取详情页正文
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

        # 分页停止条件：当前页最旧日期早于目标起始日期
        if oldest_date_on_page and oldest_date_on_page < target_from:
            break

        # 如果列表项少于预期，可能是最后一页
        if len(nodes) < 10:
            break

        page_index += 1

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
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
