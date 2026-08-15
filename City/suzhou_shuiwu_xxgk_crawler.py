import xml.etree.ElementTree as ET
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

TARGET_URL = "https://jiangsu.chinatax.gov.cn/col/col9401/index.html"
SOURCE_NAME = "苏州市税务局_信息公开"
CATEGORY = "苏州"
MAX_PAGES = 200
PAGE_SIZE = 15

# 该站列表由 TRS jpage 接口动态渲染
JPAGE_API = "https://jiangsu.chinatax.gov.cn/module/web/jpage/dataproxy.jsp"
JPAGE_PARAMS = {
    "perpage": PAGE_SIZE,
    "col": 1,
    "appid": 1,
    "webid": 18,
    "path": "/",
    "columnid": 9401,
    "sourceContentType": 1,
    "unitid": 118334,
    "webname": "国家税务总局江苏省税务局网站",
    "permissiontype": 0,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = soup.select_one("#zoomcon UCAPCONTENT") or soup.select_one("#zoomcon")
        if not content_elem:
            return ""
        for tag_name in ("script", "style", "noscript", "iframe"):
            for node in content_elem.find_all(tag_name):
                node.decompose()
        return content_elem.get_text("\n", strip=True)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _fetch_page(session, page: int):
    """请求 jpage 接口，返回 (totalpage, [li 节点列表])。"""
    params = dict(JPAGE_PARAMS)
    params["startrecord"] = (page - 1) * PAGE_SIZE + 1
    params["endrecord"] = page * PAGE_SIZE
    response = session.get(JPAGE_API, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    totalpage = int(root.findtext("totalpage") or 1)
    nodes = []
    for rec in root.iter("record"):
        frag = BeautifulSoup(rec.text or "", "html.parser")
        li = frag.find("li")
        if li is not None:
            nodes.append(li)
    return totalpage, nodes


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    oldest_date_on_page = None

    try:
        for page in range(1, MAX_PAGES + 1):
            try:
                totalpage, nodes = _fetch_page(session, page)
            except Exception as exc:
                metrics.errors.append(f"列表接口请求失败 (page={page}): {exc}")
                break

            if not nodes:
                break

            metrics.raw_item_count += len(nodes)

            for node in nodes:
                try:
                    link = node.find("a")
                    if not link:
                        metrics.invalid_item_count += 1
                        continue
                    title = (link.get("title") or "").strip()
                    if not title:
                        title = link.get_text(" ", strip=True)
                    href = (link.get("href") or "").strip()
                    date_elem = node.find("b")
                    pub_date_str = date_elem.get_text(strip=True) if date_elem else ""

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    # href 可能是协议相对 URL（//host/path）
                    article_url = urljoin(TARGET_URL, href)
                    pub_at = parse_date(pub_date_str)

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        continue

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    policies.append(
                        {
                            "title": title,
                            "url": article_url,
                            "pub_at": pub_at,
                            "content": _extract_content(session, article_url, metrics),
                            "selected": False,
                            "category": CATEGORY,
                            "source": SOURCE_NAME,
                        }
                    )
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {exc}")

            if page >= totalpage:
                break
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
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
