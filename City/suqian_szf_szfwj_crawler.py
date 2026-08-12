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


TARGET_URL = "https://www.suqian.gov.cn/cnsq/szfwj/xxgk_list.shtml"
SOURCE_NAME = "宿迁市人民政府_市政府文件"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """带重试的HTTP请求，禁用代理"""
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=timeout,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            # 站点响应头未声明 charset，requests 会默认按 ISO-8859-1
            # 解码；页面自身明确使用 UTF-8，需在读取 response.text 前纠正。
            response.encoding = "utf-8"
            return response
        except Exception as exc:
            if attempt == max_retries:
                raise


def _extract_content(session, article_url, metrics):
    """提取详情页正文和确认发布日期"""
    try:
        response = _fetch_with_retry(article_url, timeout=15)
        soup = BeautifulSoup(response.content, "html.parser")

        # 提取正文
        article_div = soup.find("div", class_="article-content")
        if article_div:
            for tag in article_div.find_all(["script", "style"]):
                tag.decompose()
            content = article_div.get_text("\n", strip=True)
        else:
            content = ""

        # 从详情页提取发文日期（用于与列表页日期核验）
        detail_date = None
        if article_div:
            text = article_div.get_text("\n", strip=True)
            # 匹配"发文日期"后面的日期
            import re
            date_match = re.search(r"发文日期[^\d]*(\d{4}-\d{2}-\d{2})", text)
            if date_match:
                detail_date = date_match.group(1)

        return content, detail_date
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return "", None


def scrape_data():
    """抓取宿迁市政府文件数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    seen_urls = set()
    session = requests.Session()

    page_index = 1
    max_pages = 100

    while page_index <= max_pages:
        page_url = TARGET_URL
        if page_index > 1:
            page_url = f"https://www.suqian.gov.cn/cnsq/szfwj/xxgk_list_{page_index}.shtml"

        try:
            response = _fetch_with_retry(page_url)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 [第{page_index}页]: {exc}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        list_div = soup.find("div", class_="list")
        if not list_div:
            break

        nodes = list_div.find_all("li")
        metrics.raw_item_count += len(nodes)

        if not nodes:
            break

        oldest_date_on_page = None

        for node in nodes:
            try:
                link = node.find("a")
                span = node.find("span")

                if not link:
                    metrics.invalid_item_count += 1
                    continue

                title = link.get_text(" ", strip=True)
                href = link.get("href", "").strip()
                raw_date = span.get_text(" ", strip=True) if span else ""

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin("https://www.suqian.gov.cn", href)

                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)

                # 从列表页解析日期
                pub_at = parse_date(raw_date) if raw_date else None

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析列表页日期: {title[:30]}... (原始: {raw_date})")
                    continue

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                # 先用列表页日期过滤
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 提取详情页正文并获取详情页日期进行核验
                content, detail_date = _extract_content(session, article_url, metrics)

                # 如果详情页有日期且与列表页不同，记录警告
                if detail_date:
                    detail_pub_at = parse_date(detail_date)
                    if detail_pub_at and pub_at != detail_pub_at:
                        metrics.errors.append(
                            f"日期核验: {title[:30]}... 列表页={raw_date}, 详情页={detail_date}, "
                            f"以详情页日期{detail_date}为准"
                        )
                        # 以详情页日期为准重新判断
                        if not is_target_date(detail_pub_at, target_from, target_to):
                            metrics.filtered_count += 1
                            metrics.target_date_count = max(0, metrics.target_date_count - 1)
                            continue
                        pub_at = detail_pub_at

                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {exc}")

        # 当最旧日期早于目标窗口起始日期时停止翻页
        if oldest_date_on_page and oldest_date_on_page < target_from:
            break

        # 每页少于10条时认为已到末尾
        if len(nodes) < 10:
            break

        page_index += 1

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
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
