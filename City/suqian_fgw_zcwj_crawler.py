"""
宿迁市发展和改革委员会_政策文件爬虫
目标栏目：https://fgw.suqian.gov.cn/sfgw/zcwj/xxgk_list.shtml
"""
import os
from urllib.parse import urljoin
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://fgw.suqian.gov.cn/sfgw/zcwj/xxgk_list.shtml"
SOURCE_NAME = "宿迁市发展和改革委员会_政策文件"
CATEGORY = "宿迁"

LIST_CONTAINER_SELECTOR = "div.list"
LIST_ITEM_SELECTOR = "div.list li"
TITLE_SELECTOR = "div.list li a"
DATE_SELECTOR = "div.list li span"

CONTENT_SELECTOR = "div.article-content#zoomcon"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://fgw.suqian.gov.cn/",
}

MAX_PAGES = 100


def _fetch_page(url, timeout=30):
    """使用urllib获取页面内容"""
    req = Request(url, headers=HEADERS)
    response = urlopen(req, timeout=timeout)
    return response.read()


def _extract_content(article_url, metrics):
    """提取详情页正文内容"""
    try:
        content_bytes = _fetch_page(article_url, timeout=15)
        soup = BeautifulSoup(content_bytes, "html.parser")

        # 查找正文容器
        content_elem = soup.select_one(CONTENT_SELECTOR)
        if not content_elem:
            return ""

        # 移除脚本和样式
        for tag in content_elem.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 提取纯文本，保留段落边界
        paragraphs = []
        for child in content_elem.children:
            if hasattr(child, 'name') and child.name:
                text = child.get_text(" ", strip=True)
                if text:
                    paragraphs.append(text)
            elif hasattr(child, 'get_text'):
                text = child.get_text(strip=True)
                if text:
                    paragraphs.append(text)

        content = "\n".join(paragraphs)
        return content.strip() if content else ""

    except HTTPError:
        metrics.errors.append(f"详情页HTTP错误: {article_url}")
        return ""
    except URLError as e:
        metrics.errors.append(f"详情页访问失败: {article_url} - {e.reason}")
        return ""
    except Exception as e:
        metrics.errors.append(f"详情页解析失败: {article_url} - {e}")
        return ""


def scrape_data():
    """抓取政策文件数据"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()

    page_index = 1
    oldest_date_on_page = None

    while page_index <= MAX_PAGES:
        # 构建当前页URL
        if page_index == 1:
            page_url = TARGET_URL
        else:
            page_url = TARGET_URL.replace("_list.", f"_list_{page_index}.")

        try:
            content_bytes = _fetch_page(page_url, timeout=30)
            soup = BeautifulSoup(content_bytes, "html.parser")

            list_container = soup.select_one(LIST_CONTAINER_SELECTOR)
            if not list_container:
                break

            list_items = list_container.find_all("li")
            if not list_items:
                break

            if page_index == 1:
                metrics.raw_item_count = len(list_items)

            for item in list_items:
                # 提取标题和链接
                link_elem = item.find("a")
                if not link_elem:
                    metrics.invalid_item_count += 1
                    continue

                title = link_elem.get_text(" ", strip=True)
                href = link_elem.get("href", "")

                if not title or not href:
                    metrics.invalid_item_count += 1
                    continue

                # 提取日期
                date_elem = item.find("span")
                date_text = date_elem.get_text(strip=True) if date_elem else ""

                if date_text:
                    pub_at = parse_date(date_text)
                else:
                    pub_at = None

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                    continue

                # 构建完整URL
                article_url = urljoin(TARGET_URL, href)

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                # 记录当前页最旧日期用于翻页判断
                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                # 日期窗口过滤
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 提取正文
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

            # 翻页停止条件：当前页最旧日期早于目标日期窗口起点
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            page_index += 1

        except HTTPError as e:
            if e.code == 404:
                # 最后一页
                break
            metrics.errors.append(f"列表页HTTP错误 (page={page_index}): {e.code}")
            break
        except URLError as e:
            metrics.errors.append(f"列表页访问失败 (page={page_index}): {e.reason}")
            break
        except Exception as e:
            metrics.errors.append(f"列表页解析失败 (page={page_index}): {e}")
            break

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

    return policies, latest_items[:5], metrics


def run():
    """执行爬虫并返回结果"""
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
