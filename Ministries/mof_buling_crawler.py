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


TARGET_URL = "https://www.mof.gov.cn/gkml/bulinggonggao/tongzhitonggao/"
SOURCE_NAME = "财政部通知公告"
CATEGORY = "中央部委"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _clean_text(element):
    lines = [
        line.strip()
        for line in element.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    return "\n".join(lines)


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        content_element = (
            soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="my_doccontent")
            or soup.find("div", class_="my_conboxzw")
            or soup.find("div", class_="mainboxerji")
            or soup.find("div", class_="content")
        )
        return _clean_text(content_element) if content_element else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        nodes = soup.select("ul.xwbd_lianbolistfrcon li")
        metrics.raw_item_count = len(nodes)

        if not nodes:
            metrics.errors.append("未找到文章列表 ul.xwbd_lianbolistfrcon li")

        for node in nodes:
            try:
                link = node.find("a")
                title = link.get_text(" ", strip=True) if link else ""
                href = (link.get("href") or "").strip() if link else ""
                pub_at = None
                for span in node.find_all("span"):
                    pub_at = parse_date(span.get_text(" ", strip=True))
                    if pub_at:
                        break

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(
                        f"列表记录核心字段缺失: {title or href or '未知条目'}"
                    )
                    continue

                article_url = urljoin(TARGET_URL, href)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                content = _extract_content(session, article_url, metrics)
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
