"""
南京市政府政策文件爬虫
目标栏目：https://www.nanjing.gov.cn/zdgk/214/400/index_17989.html
"""
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


TARGET_URL = "https://www.nanjing.gov.cn/zdgk/214/400/index_17989.html"
SOURCE_NAME = "南京市政府_政策文件"
CATEGORY = "南京"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_content(session, article_url, metrics):
    """抓取详情页正文内容"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 尝试多个正文选择器（南京市政府网站使用 .wenZhang 或 .TRS_UEDITOR）
        content_elem = (
            soup.select_one(".wenZhang")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".main_b")
            or soup.select_one(".main_bl")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".article-content")
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


def scrape_data():
    """抓取南京市政府政策文件列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 根据南京市政府网站结构，列表项在 ul.list li 或类似结构中
        # 分析页面：标题在 <a> 标签中，日期在文本中
        nodes = soup.select("ul.list li") or soup.select(".list li") or soup.select("li")

        # 过滤出有效的政策条目（包含链接的 li）
        valid_nodes = []
        for node in nodes:
            link = node.select_one("a")
            if link and link.get("href"):
                valid_nodes.append(node)

        metrics.raw_item_count = len(valid_nodes)

        for node in valid_nodes:
            try:
                link = node.select_one("a")
                title = link.get_text(" ", strip=True) if link else ""
                href = (link.get("href") or "").strip() if link else ""

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                # 优先从列表页专门的日期节点获取发布日期（<span class="d2">）
                date_elem = node.select_one("span.d2")
                pub_at = None

                if date_elem:
                    # 使用列表页明确的日期节点
                    date_text = date_elem.get_text(strip=True)
                    pub_at = parse_date(date_text)
                else:
                    # 备用：从整个节点文本中查找日期（但不能是失效日期等）
                    import re
                    full_text = node.get_text()
                    # 尝试匹配标准格式
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", full_text)
                    if date_match:
                        pub_at = parse_date(date_match.group(1))

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析日期: {title[:30]}...")
                    continue

                article_url = urljoin(TARGET_URL, href)
                metrics.valid_item_count += 1

                # 记录最新条目（来自整个列表）
                latest_items.append({"title": title, "pub_at": pub_at})

                # 日期过滤
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 抓取详情页内容
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