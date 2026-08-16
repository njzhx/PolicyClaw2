"""
盐城市应急管理局_政府信息公开爬虫
目标栏目：https://www.yancheng.gov.cn/col/col801/index.html
页面机制：汉风 xxgk 信息公开系统，列表由 /module/xxgk/search.jsp 动态加载。
          每个部门页面对应独立的 area 代码，从首页 loadDynamic 调用中提取；
          翻页通过 POST search.jsp（currpage 在 URL query 中）。
记录格式：<tr><td>索引号</td><td><a href="..." title="...">标题</a></td><td>YYYY-MM-DD</td></tr>
详情页正文：#zoom
"""
import re
import time
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


TARGET_URL = "https://www.yancheng.gov.cn/col/col801/index.html"
SOURCE_NAME = "盐城市应急管理局_政府信息公开"
CATEGORY = "盐城"
BASE_URL = "https://www.yancheng.gov.cn"
SEARCH_URL = "https://www.yancheng.gov.cn/module/xxgk/search.jsp"
# 页面 URL 中的 number 参数对应组配分类 infotypeId；无分类过滤时留空
INFOTYPE_ID = ""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": TARGET_URL,
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 30
DETAIL_SLEEP = 0.5


def _new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _extract_area(html):
    """从首页 loadDynamic 调用中提取部门 area 代码。"""
    match = re.search(
        r"loadDynamic\('/module/xxgk/search\.jsp[^']*?area=([0-9A-Za-z]+)", html
    )
    if match:
        return match.group(1)
    match = re.search(r"tree\.jsp\?area=([0-9A-Za-z]+)", html)
    return match.group(1) if match else None


def _fetch_list_page(session, area, page, metrics):
    """POST search.jsp 获取一页信息公开列表 HTML。"""
    query = (
        f"infotypeId={INFOTYPE_ID}&vc_title=&vc_number=&area={area}"
        f"&currpage={page}"
    )
    body = (
        f"infotypeId={INFOTYPE_ID}&jdid=1&divid=divlist&vc_title=&vc_number="
        f"&currpage={page}&vc_filenumber=&vc_all=&texttype=&fbtime=&area={area}"
    )
    response = session.post(
        f"{SEARCH_URL}?{query}", data=body, timeout=LIST_TIMEOUT
    )
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _parse_list_html(html, metrics):
    """解析信息公开列表行，返回 [{title, url, pub_at}]。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("tr"):
        tds = tr.select("td")
        if len(tds) != 3:
            continue
        link = tds[1].select_one("a[href]")
        date_text = tds[2].get_text(strip=True)
        pub_at = parse_date(date_text)
        if not link or not pub_at:
            continue
        rows.append((tr, link, pub_at))

    metrics.raw_item_count += len(rows)
    items = []
    for tr, link, pub_at in rows:
        try:
            title = (link.get("title") or link.get_text(" ", strip=True)).strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                metrics.invalid_item_count += 1
                continue
            metrics.valid_item_count += 1
            items.append(
                {
                    "title": title,
                    "url": urljoin(BASE_URL, href),
                    "pub_at": pub_at,
                }
            )
        except Exception as exc:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"列表记录解析失败: {exc}")
    return items


def _extract_content(session, article_url, metrics):
    """提取详情页正文；首个响应若为加速乐挑战页（过短且无正文容器）则重试一次。"""
    try:
        html = ""
        for _ in range(2):
            response = session.get(article_url, timeout=DETAIL_TIMEOUT)
            response.raise_for_status()
            html = response.content.decode("utf-8", errors="replace")
            if len(html) >= 2000 or "zoom" in html:
                break
            time.sleep(1)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()
        content_elem = (
            soup.select_one("#zoom")
            or soup.select_one(".zoom")
            or soup.select_one("#zoomcon")
            or soup.select_one("div.article")
            or soup.select_one("div.wp.article-content")
        )
        if not content_elem:
            return ""
        return content_elem.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = _new_session()

    try:
        response = session.get(TARGET_URL, timeout=LIST_TIMEOUT)
        response.raise_for_status()
        html = response.content.decode("utf-8", errors="replace")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics

    area = _extract_area(html)
    if not area:
        metrics.errors.append("列表页解析失败：未找到部门 area 代码")
        return policies, latest_items, metrics

    seen_urls = set()

    for page in range(1, MAX_PAGES + 1):
        try:
            list_html = _fetch_list_page(session, area, page, metrics)
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: 第{page}页 - {exc}")
            break

        items = _parse_list_html(list_html, metrics)
        if not items:
            if page == 1:
                metrics.errors.append("信息公开列表解析失败或无数据")
            break

        new_items = []
        for item in items:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            new_items.append(item)

        for item in new_items:
            latest_items.append({"title": item["title"], "pub_at": item["pub_at"]})

            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue

            content = _extract_content(session, item["url"], metrics)
            time.sleep(DETAIL_SLEEP)
            policies.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "pub_at": item["pub_at"],
                    "content": content,
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                }
            )

        # 最旧记录已早于目标窗口，停止翻页
        if items[-1]["pub_at"] < target_from:
            break
        time.sleep(0.5)

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
