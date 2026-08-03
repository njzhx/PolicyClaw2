"""
无锡市外事办公室（FAO）_法规文件及解读爬虫
目标栏目：https://fao.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml

列表结构分析（基于WebFetch结果）：
- 列表项格式：- [标题](URL)YYYY-MM-DD
- 标题在 <a> 标签内，日期在链接后的文本中
- 分页使用 index_{n}.shtml
- 每页约20条记录

日期规则（严格遵守）：
- 日期必须来自列表页或详情页中明确属于该文章的发布日期元素
- 保留 raw_date 用于审计
- 使用 datetime.strptime 严格解析
- 日期缺失、无效或存在歧义时，跳过该记录并报告
- 不得使用 fallback_date、default_date 或其他默认日期
- 列表页与详情页日期必须一致，否则跳过并报告
- 不得修改日期来让记录通过筛选
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


TARGET_URL = "https://fao.wuxi.gov.cn/zfxxgk/xxgkml/fgwjjjd/index.shtml"
SOURCE_NAME = "无锡市外事办公室_法规文件及解读"
CATEGORY = "无锡"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ALLOWED_HOST = "fao.wuxi.gov.cn"


def _extract_detail_date(session, article_url, metrics):
    """
    从详情页提取发布日期
    返回: (raw_date, parsed_date) 或 (None, None)

    日期规则：列表页与详情页日期必须一致，否则跳过并报告
    """
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 尝试从"发布时间"标签提取日期
        date_patterns = [
            # 格式1: 发布时间：YYYY-MM-DD HH:MM
            (r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
            # 格式2: YYYY-MM-DD
            (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
            # 格式3: YYYY年MM月DD日
            (r"(\d{4})年(\d{1,2})月(\d{1,2})日", None),
        ]

        for pattern, fmt in date_patterns:
            match = re.search(pattern, soup.get_text())
            if match:
                if fmt:
                    try:
                        parsed = datetime.strptime(match.group(1), fmt).date()
                        return match.group(1), parsed
                    except ValueError:
                        continue
                else:
                    # 处理中文日期格式
                    raw = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
                    try:
                        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
                        return raw, parsed
                    except ValueError:
                        continue

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

        # 方法1：尝试常见容器选择器
        content_elem = (
            soup.select_one("#Zoom")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".article-content")
            or soup.select_one(".main_content")
            or soup.select_one(".info-content")
            or soup.select_one(".content")
        )

        if content_elem:
            # 移除脚本和样式
            for extra in content_elem.select("script, style"):
                extra.decompose()
            text = content_elem.get_text("\n", strip=True)
            if len(text) > 100:
                return text

        # 方法2：从正文区域提取段落（基于实际页面结构）
        # 查找包含标题后的正文区域
        content_area = soup.select_one(".content-wrap") or soup.select_one(".article")
        if not content_area:
            # 尝试查找包含"发布日期"的容器作为正文区域
            content_area = soup.select_one(".detail-content") or soup.select_one(".main-content")

        if content_area:
            # 提取所有段落
            paragraphs = []
            for p in content_area.select("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 10:  # 过滤短文本
                    paragraphs.append(text)
            if paragraphs:
                return "\n".join(paragraphs)

        # 方法3：直接从 body 提取有效段落
        body = soup.find("body")
        if body:
            paragraphs = []
            for p in body.select("p"):
                text = p.get_text(strip=True)
                # 过滤短文本和明显不是正文的段落
                if len(text) > 20 and "浏览次数" not in text and "发布时间" not in text:
                    paragraphs.append(text)
            if paragraphs:
                return "\n".join(paragraphs)

        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _parse_list_page(html, base_url):
    """
    解析列表页HTML，提取标题、链接和日期
    返回记录列表，每条记录包含 title, href, raw_date
    """
    records = []
    soup = BeautifulSoup(html, "html.parser")

    # 方法1：查找所有包含 .shtml 链接的 <li> 标签
    list_items = soup.select("li")
    for li in list_items:
        link = li.select_one("a[href$='.shtml']")
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link.get("href", "").strip()
        if not title or not href:
            continue

        # 从整个li文本中提取日期
        li_text = li.get_text()
        # 查找 YYYY-MM-DD 格式日期（可能在链接后的文本中）
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", li_text)
        if date_match:
            records.append({
                "title": title,
                "href": href,
                "raw_date": date_match.group(1)
            })
            continue

        # 尝试其他日期格式
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", li_text)
        if date_match:
            raw_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
            records.append({
                "title": title,
                "href": href,
                "raw_date": raw_date
            })

    # 方法2：如果方法1没找到，尝试查找所有政策类链接
    if not records:
        all_links = soup.select("a[href*='.shtml']")
        for link in all_links:
            href = link.get("href", "").strip()
            # 过滤外链
            if "://" in href and ALLOWED_HOST not in href:
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 5:  # 跳过太短的标题
                continue

            # 查找父元素的文本中的日期
            parent = link.parent
            if parent:
                parent_text = parent.get_text()
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", parent_text)
                if date_match:
                    records.append({
                        "title": title,
                        "href": href,
                        "raw_date": date_match.group(1)
                    })

    return records


def scrape_data():
    """抓取无锡市外事办公室法规文件及解读列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    seen_urls = set()

    # 测试模式：限制页数和详情页条数
    import os
    test_mode = os.getenv("POLICYCLAW_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}
    max_pages = 1 if test_mode else 999999
    max_detail_pages = 3 if test_mode else 999999
    detail_page_count = 0

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while page_index < max_pages:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_{page_index + 1}.shtml"

            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                html = response.text
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            # 解析列表页
            records = _parse_list_page(html, base_url)

            if not records:
                if page_index == 0:
                    metrics.errors.append(f"列表页未解析到有效记录: {page_url}")
                break

            metrics.raw_item_count += len(records)

            oldest_date_on_page = None

            for record in records:
                # 测试模式：详情页最多抓3条
                if test_mode and detail_page_count >= max_detail_pages:
                    break

                try:
                    title = record["title"]
                    href = record["href"]
                    raw_date = record["raw_date"]

                    if not title or not href or not raw_date:
                        metrics.invalid_item_count += 1
                        continue

                    # 过滤外链
                    if "://" in href and ALLOWED_HOST not in href:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    # 严格解析列表页日期
                    pub_at = parse_date(raw_date)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表页日期解析失败: {raw_date} - {title[:30]}...")
                        continue

                    # 验证详情页日期是否与列表页一致
                    detail_raw_date, detail_pub_at = _extract_detail_date(session, article_url, metrics)

                    if detail_raw_date is None:
                        # 详情页无法提取日期，跳过
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"详情页日期缺失: {article_url} - {title[:30]}...")
                        continue

                    if detail_raw_date != raw_date:
                        # 列表页与详情页日期不一致，跳过
                        metrics.invalid_item_count += 1
                        metrics.errors.append(
                            f"日期冲突: 列表页={raw_date}, 详情页={detail_raw_date} - {title[:30]}..."
                        )
                        continue

                    # 测试模式：详情页计数器
                    if test_mode:
                        detail_page_count += 1

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

            # 分页提前终止
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            # 不足整页时终止
            if len(records) < 10:
                break

            # 测试模式：只抓1页
            if test_mode:
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
