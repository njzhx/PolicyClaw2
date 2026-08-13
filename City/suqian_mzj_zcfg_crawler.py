"""
宿迁市民族宗教事务局_政策法规爬虫
目标栏目：https://sqmzj.suqian.gov.cn/smzj/zcfg/xxgk_list.shtml
列表页结构：每个li包含a标签和span标签，日期在span中
详情页：使用div.article-content或div.xxgkcont作为正文容器
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


TARGET_URL = "https://sqmzj.suqian.gov.cn/smzj/zcfg/xxgk_list.shtml"
SOURCE_NAME = "宿迁市民政局_政策法规"
CATEGORY = "宿迁"
BASE_URL = "https://sqmzj.suqian.gov.cn/"
MAX_PAGES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
}


def _build_page_url(page_index):
    """构建分页URL"""
    if page_index == 0:
        return TARGET_URL
    base = TARGET_URL.replace("xxgk_list.shtml", "")
    return f"{base}xxgk_list_{page_index + 1}.shtml"


def _fetch_with_retry(url, session, max_retries=3, timeout=30):
    """带重试的HTTP请求"""
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            return response
        except Exception as exc:
            if attempt == max_retries:
                raise


def _extract_content(session, article_url, metrics):
    """从详情页提取正文

    宿迁政府网站详情页特点：
    - 正文容器：div.article-content 或 div.xxgkcont
    - 正文内容在 <ucapcontent> 标签内
    - 可能包含附件下载，正文为空是正常现象
    """
    try:
        response = _fetch_with_retry(article_url, session, timeout=15)
        soup = BeautifulSoup(response.content, "html.parser")

        # 移除脚本和样式
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 尝试多种正文选择器
        content_elem = (
            soup.select_one("div.article-content")
            or soup.select_one("div.xxgkcont")
            or soup.select_one("ucapcontent")
            or soup.select_one("div.content")
        )

        if content_elem:
            # 移除元数据表格（如果在正文容器内）
            table = content_elem.select_one("table")
            if table:
                table.decompose()

            # 获取文本
            text = content_elem.get_text("\n", strip=True)
            # 清理多余空白
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            return "\n".join(lines)

        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取政策法规数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    page_index = 0

    try:
        while page_index < MAX_PAGES:
            page_url = _build_page_url(page_index)

            try:
                response = _fetch_with_retry(page_url, session)
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")

            # 查找列表容器
            list_container = soup.select_one(".list")
            if not list_container:
                if page_index == 0:
                    metrics.errors.append("列表页未找到.list容器，可能是网站结构变化")
                break

            # 查找所有li节点
            li_nodes = list_container.select("li")

            if not li_nodes:
                if page_index == 0:
                    metrics.errors.append("列表页未找到任何li节点")
                break

            # 提取有效条目：包含链接和日期的li
            valid_items = []
            for li in li_nodes:
                a = li.select_one("a")
                if not a:
                    continue
                href = a.get("href") or ""
                title = a.get_text(strip=True)

                # 过滤掉导航链接，只保留政策文章链接
                if not href or "javascript" in href.lower():
                    continue
                if not title or len(title) < 3:
                    continue

                # 提取日期：通常是最后一个span
                spans = li.select("span")
                pub_date_str = ""
                if spans:
                    # 取最后一个span作为日期
                    pub_date_str = spans[-1].get_text(strip=True)

                valid_items.append({
                    "title": title,
                    "href": href,
                    "pub_date_str": pub_date_str,
                })

            if not valid_items:
                break

            metrics.raw_item_count += len(valid_items)

            # 记录当前页最旧日期用于翻页控制
            oldest_date_on_page = None

            for item in valid_items:
                try:
                    title = item["title"]
                    href = item["href"]
                    pub_date_str = item["pub_date_str"]

                    if not pub_date_str:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"未找到发布日期: {title[:30]}")
                        continue

                    # 标准化日期
                    pub_at = parse_date(pub_date_str)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {pub_date_str} - {title[:30]}")
                        continue

                    # 构建详情URL
                    article_url = urljoin(BASE_URL, href)
                    metrics.valid_item_count += 1

                    # 记录最旧日期
                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    # latest_items 来自整个列表，不只是目标日期
                    latest_items.append({"title": title, "pub_at": pub_at})

                    # 日期过滤
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

            # 翻页控制：如果当前页最旧日期早于目标窗口起始日期，停止翻页
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            # 每页条目少于5条认为已到末尾
            if len(valid_items) < 5:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"抓取过程异常: {exc}")

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
