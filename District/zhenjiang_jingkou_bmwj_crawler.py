# -*- coding: utf-8 -*-
"""镇江市京口区_部门信息公开 爬虫。

列表页为部门信息公开导航页，页面上列各部门链接，需逐个进入
部门页面提取文件。共享抓取逻辑见 zhenjiang_district_common.py。
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit, urldefrag

from bs4 import BeautifulSoup
from crawler_core import CrawlerMetrics, CrawlerRunResult, get_crawl_date_window, is_target_date, parse_date
from db_utils import save_to_policy
from District.zhenjiang_district_common import (
    new_session, fetch_text, extract_content, _extract_dept_links, META_REFRESH_RE, HEADERS,
)


TARGET_URL = "https://www.jingkou.gov.cn/jingkou/bmxxgk/common_list_m.shtml"
SOURCE_NAME = "镇江市京口区_部门信息公开"
CATEGORY = "镇江_京口区"


def _discover_department_api(department_url):
    """Resolve one verified department disclosure page to its official API."""
    session = new_session()
    try:
        html = fetch_text(session, department_url)
        soup = BeautifulSoup(html, "html.parser")
        meta = soup.find("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)})
        redirect = META_REFRESH_RE.search(meta.get("content", "")) if meta else None
        if redirect:
            department_url = urljoin(department_url, redirect.group(1))
            html = fetch_text(session, department_url)
            soup = BeautifulSoup(html, "html.parser")
        links = list(dict.fromkeys(
            urljoin(department_url, a["href"])
            for a in soup.select("a[href]")
            if "".join(a.get_text().split()) == "法定主动公开内容"
        ))
        if len(links) != 1 or urlsplit(links[0]).hostname != "www.jingkou.gov.cn":
            raise ValueError("[CHANNEL_MISMATCH] 未确认部门法定主动公开栏目")
        list_url = links[0]
        html = fetch_text(session, list_url)
        soup = BeautifulSoup(html, "html.parser")
        if not soup.select_one('script[src*="load_gkml_list.js"]'):
            raise ValueError("[CHANNEL_MISMATCH] 不是已验证的栏目引用列表模板")
        match = re.search(r'channelId\s*=\s*["\']([a-f0-9]{32})["\']', html)
        if not match:
            raise ValueError("[CHANNEL_MISMATCH] 缺少栏目编号")
        return urljoin(TARGET_URL, "/u/search/listByChannelRef/" + match.group(1)), ""
    except Exception as exc:
        return "", f"部门列表页抓取失败: {department_url} - {exc}"


def scrape_data():
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session, seen = new_session(), set()
    try:
        soup = BeautifulSoup(fetch_text(session, TARGET_URL), "html.parser")
        departments = _extract_dept_links(soup, TARGET_URL)
        if not departments:
            raise ValueError("部门目录为空")
    except Exception as exc:
        metrics.errors.append(f"目录页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    with ThreadPoolExecutor(max_workers=min(4, len(departments))) as executor:
        discovered = list(executor.map(
            _discover_department_api,
            (department_url for _, department_url in departments),
        ))
    for (_, department_url), (api, discovery_error) in zip(departments, discovered):
        deadline = float(os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH") or 0)
        if deadline and time.time() + 15 >= deadline:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门遍历预算不足: {department_url}")
            break
        if discovery_error:
            metrics.errors.append(discovery_error)
            continue
        signatures = set()
        for page in range(1, 501):
            if deadline and time.time() + 15 >= deadline:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 部门分页预算不足: {api} page={page}")
                break
            try:
                response = session.post(api, data={"levelType": "", "title": "", "syh": "",
                                                   "currentPage": page, "number": 100},
                                        headers=HEADERS, timeout=30)
                response.raise_for_status()
                data = response.json()["data"]["page"]
                items, total = data.get("list") or [], int(data["allRow"])
                if int(data.get("pageSize") or 100) != 100:
                    raise ValueError("分页大小与请求不一致，拒绝跳过记录")
                if not isinstance(items, list):
                    raise ValueError("列表数据类型异常")
            except Exception as exc:
                metrics.errors.append(f"列表API抓取失败: {api} page={page} - {exc}")
                break
            if not items:
                if (page - 1) * 100 < total:
                    metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前空页: {api} page={page}")
                break
            signature = tuple(str(x.get("URL_COMP")) if isinstance(x, dict) else repr(x) for x in items)
            if signature in signatures:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {api} page={page}")
                break
            signatures.add(signature)
            metrics.raw_item_count += len(items)
            page_dates = []
            for item in items:
                try:
                    title = str(item.get("TITLE") or "").strip()
                    href = str(item.get("URL_COMP") or "").strip()
                    pub_at = parse_date(item.get("PC_GKFWRQ_FORMATED"))
                    if not title or not href or not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"列表记录标题、链接或日期无效: {href or api}")
                        continue
                    page_dates.append(pub_at)
                    url = urldefrag(urljoin(TARGET_URL, href))[0]
                    if url in seen:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen.add(url)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue
                    policies.append({"title": title, "url": url, "pub_at": pub_at,
                                     "content": extract_content(session, url, metrics),
                                     "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
                except Exception as exc:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录解析失败: {api} - {exc}")
            if page * 100 >= total:
                break
            # The official API is not strictly ordered inside a page, but live
            # verification of every department confirms that each successive
            # page's newest publication date moves backwards.  Only stop when
            # every record on this page has a valid date and even the newest is
            # older than the requested window, so pinned/locally disordered
            # records on the current page cannot be skipped.
            if len(page_dates) == len(items) and max(page_dates) < target_from:
                break
        else:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {api}")
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not item.get("content") for item in policies)
    return policies, sorted(latest_items, key=lambda x: x["pub_at"], reverse=True)[:5], metrics


def run():
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(items=processed_items, latest_items=latest_items, metrics=metrics,
                            api_push_result=api_push_result)


if __name__ == "__main__":
    run()
