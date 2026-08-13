"""
宿迁市科学技术局_政策法规爬虫
目标栏目：https://kjj.suqian.gov.cn/skjk/zcfg/xxgk_list.shtml
列表结构：ul.listContent > li，每个li包含a[href]链接和span日期
正文容器：div.article-content（优选）、div.xxgkcont（降级）
分页格式：xxgk_list_2.shtml, xxgk_list_3.shtml, ...
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


TARGET_URL = "https://kjj.suqian.gov.cn/skjk/zcfg/xxgk_list.shtml"
SOURCE_NAME = "宿迁市科学技术局_政策法规"
CATEGORY = "宿迁"
HOST = "https://kjj.suqian.gov.cn"
MAX_PAGES = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _build_page_url(page_index):
    """构建分页URL。第1页为列表首页，第2页及之后为xxgk_list_N.shtml。"""
    if page_index == 0:
        return TARGET_URL
    base = TARGET_URL.replace("xxgk_list.shtml", "")
    return f"{base}xxgk_list_{page_index + 1}.shtml"


def _is_valid_kjj_link(href):
    """判断是否为有效的科技局政策法规详情链接。"""
    if not href:
        return False
    excluded = ("apply", "sqgk", "ysqgk", "login", "register", "ysq", "sq")
    href_lower = href.lower()
    if any(kw in href_lower for kw in excluded):
        return False
    # 详情页路径格式：/skjk/zcfg/YYYYMM/hash.shtml
    return "/skjk/zcfg/" in href


def _clean_title(text):
    """清理标题中的多余空白。"""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _remove_duplicate_title_prefix(text, title):
    """如果正文开头重复了标题，将其删除。"""
    if not text or not title:
        return text
    title_stripped = title.strip()
    if text.strip().startswith(title_stripped):
        remainder = text.strip()[len(title_stripped):].strip()
        if remainder:
            return remainder
    return text


def _extract_title_from_detail(soup, fallback_title):
    """从详情页提取真实标题，删除末尾的机构名称后缀。"""
    title_tag = soup.find("title")
    if not title_tag:
        return fallback_title
    raw = _clean_title(title_tag.get_text(" ", strip=True))
    for suffix in (
        "-宿迁市科学技术局",
        " - 宿迁市科学技术局",
        " -宿迁市科学技术局",
        "_宿迁市科学技术局",
        "__宿迁市科学技术局",
        "宿迁市科学技术局",
    ):
        if raw.endswith(suffix):
            return raw[: -len(suffix)].strip()
    return raw


def _extract_content(session, article_url, metrics, fallback_title=""):
    """抓取详情页正文，优先使用.article-content。"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 移除脚本和样式
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 从详情页提取真实标题
        detail_title = _extract_title_from_detail(soup, fallback_title)

        # 优先使用.article-content（最干净）
        content_elem = soup.select_one("div.article-content")
        if not content_elem:
            # 降级使用ucapcontent
            content_elem = soup.select_one("ucapcontent")
        if not content_elem:
            content_elem = soup.select_one("div.xxgkcont")

        if not content_elem:
            return "", detail_title

        # 移除噪声
        for noise_sel in (".xgxx", ".related", ".share", ".info"):
            noise = content_elem.select_one(noise_sel)
            if noise:
                noise.decompose()

        # 移除h1（正文不应包含标题）
        for h1 in content_elem.select("h1"):
            h1.decompose()

        # 提取纯文本
        text = content_elem.get_text("\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        content = "\n".join(lines)

        # 去除正文开头的标题重复
        content = _remove_duplicate_title_prefix(content, detail_title)

        return content, detail_title
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return "", fallback_title


def scrape_data():
    """抓取宿迁市科学技术局政策法规列表。"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()

    session = requests.Session()
    session.trust_env = False  # 禁用环境变量代理

    page_index = 0

    try:
        while page_index < MAX_PAGES:
            page_url = _build_page_url(page_index)

            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                if response.status_code == 404:
                    break
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
            except Exception as exc:
                metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")
            li_nodes = soup.select("ul.listContent > li")

            if not li_nodes:
                if page_index == 0:
                    metrics.errors.append("列表页未找到 ul.listContent > li")
                break

            # 过滤出有效的政策法规条目
            valid_items = []
            for li in li_nodes:
                a = li.select_one("a")
                if not a:
                    continue
                href = (a.get("href") or "").strip()
                if not _is_valid_kjj_link(href):
                    continue

                title = _clean_title(a.get_text(" ", strip=True))
                span = li.select_one("span")
                pub_date_str = _clean_title(span.get_text() if span else "")

                if not title or not pub_date_str:
                    continue

                pub_at = parse_date(pub_date_str)
                if not pub_at:
                    continue

                valid_items.append({
                    "title": title,
                    "href": href,
                    "pub_date_str": pub_date_str,
                    "pub_at": pub_at,
                })

            if not valid_items:
                if page_index == 0:
                    metrics.errors.append("列表页未找到有效条目，可能是选择器失效或网站结构变化")
                break

            metrics.raw_item_count += len(valid_items)

            for item in valid_items:
                try:
                    title = item["title"]
                    href = item["href"]
                    pub_at = item["pub_at"]

                    article_url = urljoin(HOST, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    # 详情页会返回真实标题
                    content, final_title = _extract_content(
                        session, article_url, metrics, title
                    )
                    if final_title and final_title != title:
                        latest_items[-1]["title"] = final_title

                    policies.append({
                        "title": final_title or title,
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

            # 跨页日期窗口：如果当前页最旧日期早于目标起始日期，停止翻页
            if valid_items:
                last_pub_at = valid_items[-1]["pub_at"]
                if last_pub_at and last_pub_at < target_from:
                    break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"抓取过程异常: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )

    return policies, latest_items[:5], metrics


def run():
    """执行抓取并保存数据。"""
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
