"""
无锡市体育局_法规文件及解读爬虫
目标栏目: https://tyj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml
"""
import re
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


TARGET_URL = "https://tyj.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市体育局_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PAGE_SIZE = 20


def _fetch_with_retry(url, max_retries=3, timeout=30):
    """GET 请求，最多重试 max_retries 次。"""
    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            if attempt == max_retries:
                raise


def _extract_content(article_url, metrics):
    """抓取详情页正文内容"""
    try:
        html = _fetch_with_retry(article_url, timeout=15)

        # 尝试多个可能的正文容器
        patterns = [
            r'<div[^>]*class="[^"]*TRS_Editor[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*detail[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="[^"]*Zoom[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*article[^"]*"[^>]*>(.*?)</div>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                content_div = match.group(1)
                # 移除脚本、样式和标签
                content = re.sub(r'<script[^>]*>.*?</script>', '', content_div, flags=re.DOTALL)
                content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                content = re.sub(r'<[^>]+>', '\n', content)
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = content.strip()
                if content:
                    return content

        # 如果没找到，尝试获取正文区域
        # 查找主要正文容器
        main_patterns = [
            r'<div[^>]*class="[^"]*main[^"]*"[^>]*>(.*?)</div>\s*</div>',
            r'<div[^>]*class="[^"]*view[^"]*"[^>]*>(.*?)</div>\s*</div>',
        ]
        for pattern in main_patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                content = match.group(1)
                content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
                content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                content = re.sub(r'<[^>]+>', '\n', content)
                content = re.sub(r'\n{3,}', '\n\n', content)
                return content.strip()

        # 如果还是没找到，尝试获取整个 body 的文本
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
        if body_match:
            body = body_match.group(1)
            body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
            body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
            body = re.sub(r'<[^>]+>', '\n', body)
            body = re.sub(r'\n{3,}', '\n\n', body)
            return body.strip()

        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取无锡市体育局法规文件及解读列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index + 1}.shtml"

            html = _fetch_with_retry(page_url)

            # 解析列表：查找所有 li 列表项
            # 格式: <li><a href="...">标题</a><span>日期</span></li>
            li_pattern = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL)
            li_matches = li_pattern.findall(html)

            if not li_matches:
                break

            page_raw_count = len(li_matches)
            metrics.raw_item_count += page_raw_count
            oldest_date_on_page = None

            for li_html in li_matches:
                try:
                    # 提取链接和标题
                    link_match = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', li_html, re.DOTALL)
                    # 提取日期
                    date_match = re.search(r'<span[^>]*>(\d{4}-\d{2}-\d{2})</span>', li_html)

                    if not link_match:
                        metrics.invalid_item_count += 1
                        continue

                    href = link_match.group(1).strip()
                    title_html = link_match.group(2).strip()
                    # 移除HTML标签获取纯文本标题
                    title = re.sub(r'<[^>]+>', '', title_html).strip()
                    raw_date = date_match.group(1).strip() if date_match else None

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    pub_at = parse_date(raw_date) if raw_date else None

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析日期: {title[:30]}...")
                        continue

                    article_url = urljoin(TARGET_URL, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    # 记录页面最旧日期用于分页判断
                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    # 日期过滤
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    # 抓取详情页内容
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

            # 分页停止条件：最旧日期早于目标窗口起始
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
            # 如果当前页数量少于每页数量，说明是最后一页
            if page_raw_count < PAGE_SIZE:
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
