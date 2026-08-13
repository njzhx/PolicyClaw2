"""
宿迁市数据局_通知公告爬虫
目标栏目：https://xzspj.suqian.gov.cn/sxzspj/tzgg/list.shtml
网页机构名称：宿迁市数据局（xzspj为历史域名）
列表页结构：每个li包含a标签，日期在a标签后面的文本中
分页格式：list_2.shtml, list_3.shtml 等
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


TARGET_URL = "https://xzspj.suqian.gov.cn/sxzspj/tzgg/list.shtml"
SOURCE_NAME = "宿迁市数据局_通知公告"
CATEGORY = "宿迁"
BASE_URL = "https://xzspj.suqian.gov.cn"
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
    return f"https://xzspj.suqian.gov.cn/sxzspj/tzgg/list_{page_index + 1}.shtml"


def _extract_content(session, article_url, metrics):
    """从详情页提取正文

    宿迁市数据局详情页特点：
    - 正文在标题下方的段落中
    - 可能包含附件下载链接
    """
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 移除脚本和样式
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 尝试多种正文选择器
        # 常见结构：div.article-content, div.content, div.xxgkcont, body
        content_elem = (
            soup.select_one("div.article-content")
            or soup.select_one("div.content")
            or soup.select_one("div.xxgkcont")
            or soup.select_one("div.content-wrap")
        )

        if content_elem:
            # 移除导航、相关推荐、附件等噪音
            for noise in content_elem.select(".xgxx, .related, .share, .attachment, .back-to-top"):
                noise.decompose()

            # 移除h1标题（正文不应包含标题）
            for h1 in content_elem.select("h1"):
                h1.decompose()

            text = content_elem.get_text("\n", strip=True)
            # 清理多余空白
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            return "\n".join(lines)

        return ""
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

    page_index = 0

    try:
        while page_index < MAX_PAGES:
            page_url = _build_page_url(page_index)
            print(f"[DEBUG] 正在抓取第 {page_index + 1} 页: {page_url}")

            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                response.raise_for_status()
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")
            # 查找所有li节点中的a标签
            li_nodes = soup.select("li")

            if not li_nodes:
                if page_index == 0:
                    metrics.errors.append("列表页未找到任何li节点")
                break

            # 提取有效条目
            valid_items = []
            for li in li_nodes:
                a = li.select_one("a")
                if not a:
                    continue

                href = a.get("href") or ""
                # 只保留包含 /tzgg/ 的链接
                if "/tzgg/" not in href:
                    continue

                title = a.get_text(strip=True)
                if not title:
                    continue

                # 提取日期：从li的完整文本中匹配日期格式 YYYY-MM-DD
                import re
                li_text = li.get_text()
                date_match = re.search(r"\[?(\d{4}-\d{2}-\d{2})\]?", li_text)
                pub_date_str = date_match.group(1) if date_match else ""

                valid_items.append({
                    "title": title,
                    "href": href,
                    "pub_date_str": pub_date_str,
                })

            if not valid_items:
                if page_index == 0:
                    metrics.errors.append("列表页未找到有效条目")
                break

            metrics.raw_item_count += len(valid_items)

            for item in valid_items:
                try:
                    title = item["title"]
                    href = item["href"]
                    pub_date_str = item["pub_date_str"]

                    if not pub_date_str:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"未找到发布日期: {title[:30]}")
                        continue

                    pub_at = parse_date(pub_date_str)
                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {pub_date_str} - {title[:30]}")
                        continue

                    article_url = urljoin(BASE_URL, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

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

            # 检查是否需要继续翻页
            if valid_items:
                last_pub_date_str = valid_items[-1]["pub_date_str"]
                if last_pub_date_str:
                    last_date = parse_date(last_pub_date_str)
                    if last_date and last_date < target_from:
                        print(f"[DEBUG] 第 {page_index + 1} 页最旧日期 {last_date} 早于目标起始日期 {target_from}，停止翻页")
                        break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"抓取过程异常: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

    # 列表中可能包含置顶的旧文章，不能直接把页面顺序当作发布时间顺序。
    latest_items.sort(key=lambda item: item["pub_at"], reverse=True)

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
