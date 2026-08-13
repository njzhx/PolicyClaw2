import json
import re
from datetime import date
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


TARGET_URL = "https://mzw.yangzhou.gov.cn/zcfg/zcjd/"
SOURCE_NAME = "扬州市民政局_政策解读"
CATEGORY = "扬州"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
}


def _fetch(url, timeout=30):
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp
        except Exception as exc:
            if attempt == 2:
                raise
    return None


def _extract_content(article_url, metrics):
    try:
        resp = _fetch(article_url, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        for sel in [
            "div.content#zoom",
            "#zoom",
            ".bt-content.zoom.jpaas-ai-content",
            ".bt-content",
            ".article-content",
            ".TRS_Editor",
        ]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.find_all(["script", "style"]):
                    tag.decompose()
                text = elem.get_text("\n", strip=True)
                if text:
                    return text
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    seen_urls = set()

    try:
        resp = _fetch(TARGET_URL, timeout=30)
        soup = BeautifulSoup(resp.content, "html.parser")
        script_tag = soup.select_one('script[parsetype="bulidstatic"]')
        if not script_tag:
            metrics.errors.append("未找到script[parsetype='bulidstatic']")
            return policies, latest_items, metrics

        api_url = script_tag.get("url") or script_tag.get("src")
        if not api_url:
            metrics.errors.append("script 缺少 url/src 属性")
            return policies, latest_items, metrics

        api_url = urljoin(TARGET_URL, api_url)

        querydata_str = script_tag.get("querydata") or script_tag.get("data")
        if not querydata_str:
            metrics.errors.append("script 缺少 querydata/data 属性")
            return policies, latest_items, metrics

        querydata = {}
        try:
            querydata = json.loads(querydata_str.replace("'", '"'))
        except Exception:
            metrics.errors.append(f"querydata 解析失败: {querydata_str[:100]}")
            return policies, latest_items, metrics

        rows = min(int(querydata.get("rows", 20)), 20)
        page_index = 1

        while True:
            params = dict(querydata)
            if page_index > 1:
                param_json_str = json.dumps(
                    {"pageNo": page_index, "pageSize": rows}, ensure_ascii=False
                )
                params["paramJson"] = param_json_str

            try:
                api_resp = session.get(api_url, params=params, headers=HEADERS, timeout=30)
                api_resp.raise_for_status()
                api_resp.encoding = "utf-8"
            except Exception as exc:
                metrics.errors.append(f"API请求失败 [第{page_index}页]: {exc}")
                break

            data = api_resp.json()
            html_content = data.get("data", {}).get("html", "")
            if not html_content:
                if page_index == 1:
                    metrics.errors.append("API响应无HTML内容")
                break

            li_soup = BeautifulSoup(html_content, "html.parser")
            nodes = li_soup.find_all("li")
            if not nodes and page_index == 1:
                metrics.errors.append("API返回无li节点")
                break
            if not nodes:
                break

            metrics.raw_item_count += len(nodes)
            oldest_date = None

            for node in nodes:
                try:
                    link = node.find("a")
                    if not link:
                        metrics.invalid_item_count += 1
                        continue

                    title = link.get_text(" ", strip=True)
                    href = (link.get("href") or "").strip()
                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    article_url = urljoin(TARGET_URL, href)

                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)

                    pub_at = None
                    for span in node.find_all("span"):
                        date_match = re.search(
                            r"(\d{4})[/\-\.年](\d{1,2})[/\-\.月](\d{1,2})日?",
                            span.get_text(strip=True),
                        )
                        if date_match:
                            try:
                                pub_at = date(
                                    int(date_match.group(1)),
                                    int(date_match.group(2)),
                                    int(date_match.group(3)),
                                )
                                break
                            except ValueError:
                                pass

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        continue

                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if oldest_date is None or pub_at < oldest_date:
                        oldest_date = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    content = _extract_content(article_url, metrics)
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
                    metrics.errors.append(f"列表解析失败: {exc}")

            if oldest_date and oldest_date < target_from:
                break
            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页失败: {exc}")

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
