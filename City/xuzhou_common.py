# -*- coding: utf-8 -*-
"""徐州市系列爬虫的共享抓取逻辑。

本模块不是爬虫入口（文件名不以 _crawler.py 结尾，不会被
crawler_manager 动态发现），仅供 City 目录下的徐州各单站爬虫复用：

1. ``scrape_gov_site``：适用于徐州各部门统一的政府信息公开平台
   （``{host}/dynamic/zwgk/govInfoPub.html?categorynum=xxx``），
   列表数据通过 ``POST {host}/EWB-FRONT/rest/lightfrontaction/getgovinfolist``
   JSON 接口获取，``siteGuid`` 从各站 ``/js/webBuilderCommon.js`` 动态提取；
2. ``scrape_zrzy_site``：适用于徐州市自然资源和规划局所在的
   ``zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action`` 服务端渲染列表
   （``td.nlist`` 结构，POST ``cpage`` 翻页）；
3. ``scrape_credit_site``：适用于信用徐州（``www.xuzhoucredit.gov.cn``）
   Vue 站点，列表数据通过 ``GET /wcm/content/news_list.json`` 获取，
   父栏目下多个子栏目聚合抓取；
4. ``scrape_ggzy_site``：适用于徐州市公共资源交易中心"政策文件"导航页指向的
   江苏省公共资源交易平台法规接口
   （``GET jsggzy.jszwfw.gov.cn/.../zcfgInfoListAction.action``）。
"""

import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

LIST_TIMEOUT = 30
DETAIL_TIMEOUT = 15
MAX_PAGES = 50

# 统一信息公开平台详情页正文候选选择器，按优先级排列
GOV_CONTENT_SELECTORS = (
    "#ivs_content",
    "div.mian-cont",
    ".TRS_Editor",
    "#zoom",
    ".view.TRS_UEDITOR",
    ".TRS_UEDITOR",
)

# 通用正文候选选择器（外部站点详情页兜底）
GENERIC_CONTENT_SELECTORS = (
    "#ivs_content",
    "div.mian-cont",
    ".TRS_Editor",
    "#zoom",
    ".TRS_UEDITOR",
    ".editorContent-box",
    "td[style*='line-height:28px']",
    ".article-content",
    ".content-box",
    "#content",
    ".content",
)

DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
SITE_GUID_RE = re.compile(r'siteGuid"\s*:\s*"([^"]+)"')


def new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def extract_content(session, article_url, metrics, selectors=GENERIC_CONTENT_SELECTORS):
    """提取详情页正文；失败时记录错误并返回空字符串。"""
    try:
        response = session.get(article_url, timeout=DETAIL_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                for noise in element.select("script, style, iframe"):
                    noise.decompose()
                text = element.get_text("\n", strip=True)
                if text:
                    return text
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _build_policy(item, content, category, source_name):
    return {
        "title": item["title"],
        "url": item["url"],
        "pub_at": item["pub_at"],
        "content": content,
        "selected": False,
        "category": category,
        "source": source_name,
    }


def _finalize(policies, latest_candidates, metrics):
    """统一收尾：最新5条按日期倒序，补齐指标。"""
    latest_sorted = sorted(
        latest_candidates, key=lambda x: x["pub_at"], reverse=True
    )
    latest_items = [
        {"title": item["title"], "pub_at": item["pub_at"]}
        for item in latest_sorted[:5]
    ]
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items, metrics


# ----------------------------------------------------------------------
# 1. 徐州各部门统一信息公开平台
# ----------------------------------------------------------------------

def _fetch_site_guid(session, host):
    """从站点 /js/webBuilderCommon.js 动态提取 siteGuid。"""
    response = session.get(host + "/js/webBuilderCommon.js", timeout=LIST_TIMEOUT)
    response.raise_for_status()
    match = SITE_GUID_RE.search(response.text)
    if not match:
        raise ValueError(f"未找到 siteGuid: {host}")
    return match.group(1)


def scrape_gov_site(host, categorynum, source_name, category, metrics=None):
    """抓取徐州统一信息公开平台（POST getgovinfolist JSON 接口）。"""
    policies = []
    latest_candidates = []
    if metrics is None:
        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    try:
        site_guid = _fetch_site_guid(session, host)
    except Exception as exc:
        metrics.errors.append(f"siteGuid 获取失败: {host} - {exc}")
        return _finalize(policies, latest_candidates, metrics)

    api_url = host + "/EWB-FRONT/rest/lightfrontaction/getgovinfolist"
    page_size = 20
    page_index = 0
    seen_urls = set()
    total = None

    while page_index < MAX_PAGES:
        payload = {
            "deptcode": "",
            "categorynum": categorynum,
            "pageIndex": page_index,
            "pageSize": page_size,
            "siteGuid": site_guid,
        }
        try:
            response = session.post(api_url, json=payload, timeout=LIST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {api_url} - {exc}")
            break

        custom = data.get("custom") or {}
        records = custom.get("data") or []
        if total is None:
            try:
                total = int(custom.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
        metrics.raw_item_count += len(records)

        if not records:
            break

        page_items = []
        for record in records:
            title = str(
                record.get("title") or record.get("realtitle") or ""
            ).strip()
            href = str(
                record.get("infourl")
                or record.get("visiturl")
                or record.get("linkurl")
                or ""
            ).strip()
            pub_at = parse_date(
                record.get("infodate") or record.get("handsdate")
            )

            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue

            article_url = urljoin(host + "/", href)
            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)
            metrics.valid_item_count += 1
            page_items.append(
                {"title": title, "url": article_url, "pub_at": pub_at}
            )

        for item in page_items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                _build_policy(
                    item,
                    extract_content(
                        session, item["url"], metrics, GOV_CONTENT_SELECTORS
                    ),
                    category,
                    source_name,
                )
            )

        if not page_items:
            break
        # 列表按发布日期倒序：整页最旧一条早于窗口起点时停止翻页
        oldest_on_page = min(item["pub_at"] for item in page_items)
        if oldest_on_page < target_from:
            break
        if total is not None and (page_index + 1) * page_size >= total:
            break
        page_index += 1

    return _finalize(policies, latest_candidates, metrics)


# ----------------------------------------------------------------------
# 2. 徐州市自然资源和规划局（zrzy.jiangsu.gov.cn gtapp 平台）
# ----------------------------------------------------------------------

def scrape_zrzy_site(list_url, source_name, category, metrics=None):
    """抓取 zrzy.jiangsu.gov.cn nrglIndex.action 服务端渲染列表。

    第 1 页 GET，后续页 POST ``cpage=n``；条目为 ``td.nlist``
    （内含链接与日期文本）。
    """
    policies = []
    latest_candidates = []
    if metrics is None:
        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    page_index = 1
    seen_urls = set()
    while page_index <= MAX_PAGES:
        try:
            if page_index == 1:
                response = session.get(list_url, timeout=LIST_TIMEOUT)
            else:
                response = session.post(
                    list_url, data={"cpage": str(page_index)},
                    timeout=LIST_TIMEOUT,
                )
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
        except Exception as exc:
            metrics.errors.append(f"列表页抓取失败: {list_url} 第{page_index}页 - {exc}")
            break

        containers = soup.select("td.nlist")
        metrics.raw_item_count += len(containers)

        page_items = []
        for container in containers:
            link = container.select_one("a[href]")
            if not link:
                metrics.invalid_item_count += 1
                continue
            href = (link.get("href") or "").strip()
            title = (link.get("title") or "").strip() or link.get_text(
                " ", strip=True
            )
            date_match = DATE_RE.search(container.get_text(" ", strip=True))
            pub_at = parse_date(date_match.group(0)) if date_match else None

            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue

            article_url = urljoin(list_url, href)
            if article_url in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(article_url)
            metrics.valid_item_count += 1
            page_items.append(
                {"title": title, "url": article_url, "pub_at": pub_at}
            )

        for item in page_items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                _build_policy(
                    item,
                    extract_content(session, item["url"], metrics),
                    category,
                    source_name,
                )
            )

        if not page_items:
            break
        oldest_on_page = min(item["pub_at"] for item in page_items)
        if oldest_on_page < target_from:
            break
        page_index += 1

    return _finalize(policies, latest_candidates, metrics)


# ----------------------------------------------------------------------
# 3. 信用徐州（Vue 站点，news_list.json 接口）
# ----------------------------------------------------------------------

CREDIT_HOME = "https://www.xuzhoucredit.gov.cn"
# “政策法规”父栏目 ID（zcgf.html 页面内 COLUMN 定义）
CREDIT_ZCGF_COLUMN_ID = "48848cee0b9041a9be1df9aabe473f8a"


def _credit_fetch_sub_columns(session):
    """获取“政策法规”父栏目下的全部子栏目。"""
    response = session.get(
        CREDIT_HOME + "/wcm/column/sub_columns.action",
        params={"id": CREDIT_ZCGF_COLUMN_ID},
        timeout=LIST_TIMEOUT,
    )
    response.raise_for_status()
    columns = response.json()
    if not isinstance(columns, list):
        return []
    return [
        (str(col.get("columnId") or "").strip(), str(col.get("columnName") or ""))
        for col in columns
        if col.get("columnId")
    ]


def scrape_credit_site(source_name, category, metrics=None):
    """抓取信用徐州“政策法规”栏目（聚合全部子栏目）。"""
    policies = []
    latest_candidates = []
    if metrics is None:
        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    try:
        sub_columns = _credit_fetch_sub_columns(session)
    except Exception as exc:
        metrics.errors.append(f"子栏目获取失败: {exc}")
        return _finalize(policies, latest_candidates, metrics)

    if not sub_columns:
        sub_columns = [(CREDIT_ZCGF_COLUMN_ID, "政策法规")]

    page_size = 20
    seen_urls = set()
    for column_id, column_name in sub_columns:
        page_index = 0
        while page_index < MAX_PAGES:
            try:
                response = session.get(
                    CREDIT_HOME + "/wcm/content/news_list.json",
                    params={
                        "columnId": column_id,
                        "page": page_index,
                        "size": page_size,
                    },
                    timeout=LIST_TIMEOUT,
                )
                response.raise_for_status()
                records = response.json()
            except Exception as exc:
                metrics.errors.append(
                    f"列表API抓取失败: {column_name}({column_id}) 第{page_index}页 - {exc}"
                )
                break

            if not isinstance(records, list):
                records = []
            metrics.raw_item_count += len(records)
            if not records:
                break

            page_items = []
            for record in records:
                title = str(record.get("title") or "").strip()
                href = str(record.get("url") or "").strip()
                pub_at = parse_date(record.get("publishDate"))

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(CREDIT_HOME + "/", href)
                if article_url in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(article_url)
                metrics.valid_item_count += 1
                page_items.append(
                    {"title": title, "url": article_url, "pub_at": pub_at}
                )

            for item in page_items:
                latest_candidates.append(item)
                if not is_target_date(item["pub_at"], target_from, target_to):
                    metrics.filtered_count += 1
                    continue
                policies.append(
                    _build_policy(
                        item,
                        extract_content(
                            session, item["url"], metrics,
                            ("div.content-box",) + GENERIC_CONTENT_SELECTORS,
                        ),
                        category,
                        source_name,
                    )
                )

            if not page_items:
                break
            oldest_on_page = min(item["pub_at"] for item in page_items)
            if oldest_on_page < target_from:
                break
            if len(records) < page_size:
                break
            page_index += 1

    return _finalize(policies, latest_candidates, metrics)


# ----------------------------------------------------------------------
# 4. 徐州市公共资源交易中心（省平台 zcfgInfoListAction 接口）
# ----------------------------------------------------------------------

GGZY_API = (
    "http://jsggzy.jszwfw.gov.cn"
    "/EpointWebBuilder_jsggzy/zcfgInfoListAction.action"
)
GGZY_CATEGORY_NUM = "008"


def scrape_ggzy_site(source_name, category, metrics=None):
    """抓取徐州公共资源交易“政策文件”导航指向的省平台法规接口。

    响应 ``custom`` 字段为 JSON 字符串，含 ``Table``（href/title/infodate）
    与 ``totalcount``；href 为各部委/省级网站绝对链接。
    """
    policies = []
    latest_candidates = []
    if metrics is None:
        metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = new_session()

    page_size = 15
    page_index = 0
    total = None
    seen_urls = set()

    while page_index < MAX_PAGES:
        try:
            response = session.get(
                GGZY_API,
                params={
                    "cmd": "getInfolist",
                    "categorynum": GGZY_CATEGORY_NUM,
                    "title": "",
                    "pageSize": page_size,
                    "pageIndex": page_index,
                },
                timeout=LIST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            custom = json.loads(payload.get("custom") or "{}")
        except Exception as exc:
            metrics.errors.append(f"列表API抓取失败: {GGZY_API} - {exc}")
            break

        records = custom.get("Table") or []
        if total is None:
            try:
                total = int(custom.get("totalcount") or 0)
            except (TypeError, ValueError):
                total = 0
        metrics.raw_item_count += len(records)

        if not records:
            break

        page_items = []
        for record in records:
            title = str(record.get("title") or "").strip()
            href = str(record.get("href") or "").strip()
            pub_at = parse_date(record.get("infodate"))

            if not title or not href or not pub_at:
                metrics.invalid_item_count += 1
                continue
            if not href.startswith(("http://", "https://")):
                metrics.invalid_item_count += 1
                continue

            if href in seen_urls:
                metrics.duplicate_policy_count += 1
                continue
            seen_urls.add(href)
            metrics.valid_item_count += 1
            page_items.append({"title": title, "url": href, "pub_at": pub_at})

        for item in page_items:
            latest_candidates.append(item)
            if not is_target_date(item["pub_at"], target_from, target_to):
                metrics.filtered_count += 1
                continue
            policies.append(
                _build_policy(
                    item,
                    extract_content(session, item["url"], metrics),
                    category,
                    source_name,
                )
            )

        if not page_items:
            break
        oldest_on_page = min(item["pub_at"] for item in page_items)
        if oldest_on_page < target_from:
            break
        if total is not None and (page_index + 1) * page_size >= total:
            break
        page_index += 1

    return _finalize(policies, latest_candidates, metrics)
