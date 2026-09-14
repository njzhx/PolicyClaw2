"""
淮安市涟水县_部门发文爬虫
目标栏目：http://www.lianshui.gov.cn/cmsweb/zwgk/ls/department.html?r=000000006512793301651723f7fe04a0
说明：目录页提取部门 orgId，调用 /articleCommonController/lists.do 接口
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


TARGET_URL = "http://www.lianshui.gov.cn/cmsweb/zwgk/ls/department.html?r=000000006512793301651723f7fe04a0"
SOURCE_NAME = "淮安市涟水县_部门发文"
CATEGORY = "淮安_涟水县"
RDEPTID = "000000006512793301651723f7fe04a0"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": TARGET_URL,
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")
        content_elem = (
            soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".article-content")
            or soup.select_one("#UCAP-CONTENT")
            or soup.select_one(".TRS_Editor")
            or soup.select_one("#zoom")
            or soup.select_one(".Custom_UnionStyle")
            or soup.select_one(".article")
            or soup.select_one(".content")
        )
        if content_elem:
            for extra in content_elem.select("script, style"):
                extra.decompose()
            return content_elem.get_text("\n", strip=True)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}

    try:
        # 先调用 showDepartment 获取部门列表
        r = session.post(
            "http://www.lianshui.gov.cn/articleCommonController/showDepartment.do",
            data={"rootId": RDEPTID, "type": 5},
            headers=API_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        dept_data = r.json()
        dept_list = dept_data.get("value") or []
        org_ids = [item[0] for item in dept_list if item and len(item) > 0]
        org_ids = list(dict.fromkeys(org_ids))
        metrics.raw_item_count = len(org_ids)

        for org_id in org_ids:
            api_url = "http://www.lianshui.gov.cn/articleCommonController/lists.do"
            try:
                r = session.post(api_url, data={
                    "page": 1, "pagesize": 15, "topic": "",
                    "rdeptid": RDEPTID, "deptid": org_id,
                }, headers=API_HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
                items = (data.get("value") or {}).get("list") or []
                for item in items:
                    title = (item.get("title") or "").strip()
                    path = (item.get("path") or "").strip()
                    domain = (item.get("domain") or "").strip()
                    date_str = (item.get("releaseTime") or "").strip()
                    if not title or not path:
                        continue
                    pub_at = parse_date(date_str) if date_str else None
                    if not pub_at:
                        continue
                    article_url = (domain.rstrip("/") + "/" + path.lstrip("/")) if domain else urljoin(TARGET_URL, path)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue
                    policies.append({
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": _extract_content(session, article_url, metrics),
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    })
            except Exception as exc:
                metrics.errors.append(f"部门接口失败 orgId={org_id}: {exc}")
                continue
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {exc}")

    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
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