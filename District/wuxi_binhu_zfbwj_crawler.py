"""
无锡市滨湖区_政府办发文爬虫
目标栏目：http://www.wxbh.gov.cn/zfxxgk/sqzfxxgkml_1/fgwjjjd/qzfbgswj/index.shtml
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


TARGET_URL = "http://www.wxbh.gov.cn/zfxxgk/sqzfxxgkml_1/fgwjjjd/qzfbgswj/index.shtml"
SOURCE_NAME = "无锡市滨湖区_政府办发文"
CATEGORY = "无锡_滨湖区"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": TARGET_URL,
}


def _extract_api_params(session, metrics):
    """Read the active official search call; never fall back to a site-wide query."""
    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        scripts = "\n".join(s.get_text() for s in soup.select("script:not([src])"))
        scripts = re.sub(r"/\*.*?\*/", "", scripts, flags=re.S)
        scripts = re.sub(r"(?m)^\s*//.*$", "", scripts)
        call = re.search(r"(?:qx_wbj_|qx_parent_wbj_)?getSearchList\s*\(([^)]+)\)", scripts)
        if not call:
            raise ValueError("未找到官网栏目查询调用")
        args = [x.strip().strip("\"'") for x in call.group(1).split(",")]
        channel = args[3]
        if not re.fullmatch(r"\d+", channel):
            var = re.escape(channel)
            match = re.search(r"(?:var|let|const)\s+" + var + r"\s*=\s*[\"']([\d,]+)[\"']", scripts)
            if not match:
                raise ValueError("栏目编号变量无法解析")
            channel = match.group(1)
            children = re.findall(var + r"\s*=\s*" + var + r"\s*\+\s*[\"'],[\"']\s*\+\s*(\d+)", scripts)
            channel = ",".join([channel] + children)
        if not re.fullmatch(r"\d+(?:,\d+)*", channel) or not args[2].isdigit():
            raise ValueError("栏目编号或站点编号无效")
        return {"siteId": args[2], "channelIds": channel, "pageSize": int(args[1]),
                "searchType": args[4], "order": args[5]}
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败 [CHANNEL_MISMATCH]: {TARGET_URL} - {exc}")
        return None



def _fetch_list_via_api(session, params, page, metrics):
    api_url = urljoin(TARGET_URL, "/info_open/search")
    try:
        if not params.get("channelIds"):
            raise ValueError("拒绝空栏目编号")
        payload = {**params, "pageIndex": page}
        if "yixing.gov.cn" in TARGET_URL or "jiangyin.gov.cn" in TARGET_URL:
            payload["kind"] = 5
        response = session.post(api_url, data=payload, headers=API_HEADERS, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result.get("status") not in (0, "0"):
            raise ValueError("官网接口返回非成功状态")
        data = result["data"]
        items = data["data"]
        pages = int(data["totalPages"])
        if not isinstance(items, list):
            raise ValueError("官网列表结构变化")
        return items, pages
    except Exception as exc:
        metrics.errors.append(f"列表API抓取失败: {api_url} page={page} - {exc}")
        return [], 0



def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('#Zoom', '.TRS_UEDITOR', '.wenZhang', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '.pages_content', '#zoom', '.xlxlcont', '.Custom_UnionStyle'):
            original = soup.select_one(selector)
            if original is None:
                continue
            # Work on a copy so overlapping fallback containers remain intact.
            element = BeautifulSoup(str(original), "html.parser")
            for extra in element.select("script, style"):
                extra.decompose()
            has_media = bool(element.select("img, object, embed"))
            for link in element.select("a[href]"):
                path = link.get("href", "").split("?", 1)[0].split("#", 1)[0].lower()
                if path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")):
                    has_media = True
                    link.decompose()
            text = element.get_text("\n", strip=True)
            if text:
                return text
            media_only = media_only or has_media
            if has_media:
                break
        if media_only:
            desc = soup.select_one('meta[name="Description"], meta[name="description"]')
            summary = str(desc.get("content") or "").strip() if desc else ""
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            title_meta = soup.select_one('meta[name="ArticleTitle"]')
            article_title = str(title_meta.get("content") or "").strip() if title_meta else ""
            if summary and summary not in {title, article_title}:
                return summary
            metrics.errors.append(f"[ATTACHMENT_ONLY] 图片/附件型页面无可提取网页正文: {article_url}")
        else:
            metrics.errors.append(f"[CONTENT_MISSING] 正文选择器未命中或正文为空: {article_url}")
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""



def scrape_data():
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    params = _extract_api_params(session, metrics)
    if not params:
        return policies, latest_items, metrics
    seen_urls, seen_pages = set(), set()
    page = 1
    while page <= 500:
        items, total_pages = _fetch_list_via_api(session, params, page, metrics)
        if not items:
            if total_pages >= page:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 接口声明有数据但返回空页: {TARGET_URL} page={page}")
            break
        signature = tuple(str(x.get("url")) for x in items if isinstance(x, dict))
        if signature in seen_pages:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 接口重复页: {TARGET_URL} page={page}")
            break
        seen_pages.add(signature)
        dates = []
        for item in items:
            metrics.raw_item_count += 1
            try:
                title = str(item.get("title") or "").strip()
                href = str(item.get("url") or "").strip()
                # Match the official list's displayed writeTimeString.
                pub_at = parse_date(item.get("writeTimeString"))
                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录缺少标题/链接/发布日期: {TARGET_URL} page={page}")
                    continue
                dates.append(pub_at)
                article_url = urljoin(TARGET_URL, href)
                metrics.valid_item_count += 1
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)
                latest_items.append({"title": title, "pub_at": pub_at})
                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue
                policies.append({"title": title, "url": article_url, "pub_at": pub_at,
                                 "content": _extract_content(session, article_url, metrics),
                                 "selected": False, "category": CATEGORY, "source": SOURCE_NAME})
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {TARGET_URL} - {exc}")
        if page >= total_pages:
            break
        # Only writeTime ordering supports a publication-date early stop.
        if params["order"] == "writeTime" and len(dates) == len(items) and max(dates) < target_from:
            break
        page += 1
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限，窗口未覆盖: {TARGET_URL}")
    session.close()
    latest_items.sort(key=lambda x: x["pub_at"], reverse=True)
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not x["content"] for x in policies)
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