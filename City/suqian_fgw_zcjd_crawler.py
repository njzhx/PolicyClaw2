"""
宿迁市发展和改革委员会_政策解读爬虫
目标栏目：https://fgw.suqian.gov.cn/sfgw/zcjd/xxgk_list.shtml
分页规则：https://fgw.suqian.gov.cn/sfgw/zcjd/xxgk_list_N.shtml (N >= 1)
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


TARGET_URL = "https://fgw.suqian.gov.cn/sfgw/zcjd/xxgk_list.shtml"
BASE_URL = "https://fgw.suqian.gov.cn/"
SOURCE_NAME = "宿迁市发展和改革委员会_政策解读"
CATEGORY = "宿迁"
LIST_SELECTOR = ".list"
LIST_ITEM_SELECTOR = "li"
LIST_LINK_SELECTOR = "li a"
MAX_PAGES = 100
MAX_DETAIL_FOR_SMOKE = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _extract_detail_info(session, article_url, metrics):
    """从政策解读详情页提取正文和发布日期。

    返回 (content, pub_date_str)
    pub_date_str 为详情页元数据表格中的日期原始文本，用于与列表页交叉验证。
    """
    content = ""
    pub_date_str = None
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 提取正文
        content_elem = (
            soup.select_one(".article-content")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one("#zoom")
            or soup.select_one("div.content")
        )
        if content_elem:
            content = content_elem.get_text("\n", strip=True)

        # 从元数据表格提取发布日期
        # 表格结构：<tr><td>索引号</td><td>014...</td><td>分类</td><td>...</td></tr>
        # 包含"公开日期"标签的行，后一个 <td> 即为日期
        table = soup.select_one("table.table_content")
        if table:
            table_text = table.get_text()
            if "公开日期" in table_text:
                # 找到"公开日期"所在的 td 及其下一个 td
                for td in table.select("td"):
                    if "公开日期" in td.get_text():
                        # 找下一个兄弟 td
                        next_td = td.find_next_sibling("td")
                        if next_td:
                            text = next_td.get_text(strip=True)
                            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                                pub_date_str = text
                                break
        else:
            # 备选：从全页面 body 文本中找"公开日期"附近的日期
            body = soup.get_text()
            match = re.search(
                r"公开日期[^\d]*?(\d{4}-\d{2}-\d{2})",
                body,
            )
            if match:
                pub_date_str = match.group(1)

    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")

    return content, pub_date_str


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False

    page_index = 0
    oldest_date_on_page = None

    while page_index < MAX_PAGES:
        if page_index == 0:
            page_url = TARGET_URL
        else:
            page_url = TARGET_URL.replace(
                ".shtml", f"_{page_index + 1}.shtml"
            )

        try:
            response = session.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.content, "html.parser")

            list_container = soup.select_one(LIST_SELECTOR)
            if not list_container:
                if page_index == 0:
                    metrics.errors.append("列表容器 .list 未找到")
                break

            nodes = list_container.select(LIST_ITEM_SELECTOR)
            if not nodes:
                break

            metrics.raw_item_count += len(nodes)

            for node in nodes:
                try:
                    link = node.select_one("a")
                    if not link:
                        continue

                    title = link.get_text(" ", strip=True)
                    href = (link.get("href") or "").strip()
                    if not title or not href:
                        continue

                    article_url = urljoin(BASE_URL, href)
                    metrics.valid_item_count += 1

                    # 从详情页提取权威发布日期（列表页日期可能不可靠）
                    content, detail_pub_date = _extract_detail_info(
                        session, article_url, metrics
                    )
                    # 优先用详情页日期，其次尝试列表页日期
                    pub_at = None
                    raw_date = None
                    if detail_pub_date:
                        pub_at = parse_date(detail_pub_date)
                        raw_date = detail_pub_date
                    else:
                        date_texts = re.findall(
                            r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}",
                            node.get_text(),
                        )
                        if date_texts:
                            raw_date = date_texts[-1]
                            pub_at = parse_date(raw_date)

                    latest_items.append({"title": title, "pub_at": pub_at})

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法提取发布日期: {title[:40]}...")
                        continue

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    if not content:
                        metrics.errors.append(f"详情页正文容器未找到: {article_url}")

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

        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败 (page {page_index}): {exc}")
            break

        # 分页终止：当前页最旧日期早于目标窗口起始日期，停止翻页
        if oldest_date_on_page and oldest_date_on_page < target_from:
            break

        # 检查是否还有更多页面（列表项少于预期或为空）
        if page_index > 0 and metrics.raw_item_count == 0:
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
