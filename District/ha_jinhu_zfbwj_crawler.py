"""
淮安市金湖县_政府办发文爬虫
目标栏目：http://www.jinhu.gov.cn/cmsweb/zwgk/jh/index.html?topic=4877
说明：通过 /articleCommonController/lists.do 接口获取数据
"""
from urllib.parse import urljoin, urlparse, parse_qs

import requests

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "http://www.jinhu.gov.cn/cmsweb/zwgk/jh/index.html?topic=4877"
SOURCE_NAME = "淮安市金湖县_政府办发文"
CATEGORY = "淮安_金湖县"
RDEPTID = "0000000064cf2d8b0164d95cf92e00fc"
ZFWJ_TYPE = ""
ZFWJ_NO = ""
EXTRA_TYPE = ""

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
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        media_only = False
        for selector in ('.TRS_UEDITOR', '.article-content', '#UCAP-CONTENT', '.TRS_Editor', '#zoom', '.Custom_UnionStyle', '.article', '.content'):
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



def _fetch_list(session, page, metrics):
    parsed = urlparse(TARGET_URL)
    qs = parse_qs(parsed.query)
    topic = qs.get("topic", [""])[0]
    orgid = qs.get("orgid", [""])[0]

    post_data = {
        "page": page,
        "pagesize": 15,
        "topic": topic,
        "rdeptid": RDEPTID,
    }
    if orgid:
        post_data["deptid"] = orgid
    if EXTRA_TYPE:
        post_data["type"] = EXTRA_TYPE
    if ZFWJ_TYPE:
        post_data["zfwjtype"] = ZFWJ_TYPE
    if ZFWJ_NO:
        post_data["zfwjNo"] = ZFWJ_NO

    base = f"{parsed.scheme}://{parsed.netloc}"
    api_url = urljoin(base, "/articleCommonController/lists.do")
    try:
        r = session.post(api_url, data=post_data, headers=API_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        value = data.get("value") or {}
        return value.get("list") or [], value.get("total") or 0
    except Exception as exc:
        metrics.errors.append(f"API请求失败(page={page}): {exc}")
        return [], 0


def scrape_data():
    policies, latest_items = [], []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    session.trust_env = False
    seen, pages = set(), set()
    try:
        response = session.post(urljoin(TARGET_URL, "/openGovernmentAffairsController/getName.do"),
                                data={"topic": "4877"}, headers=API_HEADERS, timeout=30)
        response.raise_for_status()
        if response.json().get("value", {}).get("menuName") != "政府文件":
            metrics.errors.append(f"[CHANNEL_MISMATCH] 政府文件栏目校验失败: {TARGET_URL}")
            return policies, latest_items, metrics
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {exc}")
        return policies, latest_items, metrics
    for page in range(1, 501):
        items, total = _fetch_list(session, page, metrics)
        if not items:
            if total:
                metrics.errors.append(f"[PAGINATION_INCOMPLETE] 提前返回空页: {TARGET_URL} page={page}")
            break
        signature = tuple(str(x.get("path")) for x in items)
        if signature in pages:
            metrics.errors.append(f"[PAGINATION_INCOMPLETE] 重复页: {TARGET_URL} page={page}")
            break
        pages.add(signature)
        for item in items:
            metrics.raw_item_count += 1
            try:
                title = str(item.get("title") or "").strip()
                path = str(item.get("path") or "").strip()
                pub_at = parse_date(item.get("releaseTime"))
                if not title or not path or not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"列表记录缺少标题/链接/发布日期: {TARGET_URL} page={page}")
                    continue
                metrics.valid_item_count += 1
                article_url = urljoin(item.get("domain") or TARGET_URL, path)
                if article_url in seen:
                    metrics.duplicate_policy_count += 1
                    continue
                seen.add(article_url)
                # Official government-files channel contains both issuers.
                office = "政府办公室" in title or "政府办关于" in title
                if office != ("政府办发文" in SOURCE_NAME):
                    continue
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
        if page * 15 >= total:
            break
    else:
        metrics.errors.append(f"[PAGINATION_INCOMPLETE] 达到安全页数上限: {TARGET_URL}")
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
