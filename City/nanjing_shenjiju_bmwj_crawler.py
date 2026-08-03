"""
南京市审计局_部门文件爬虫
目标栏目：https://sjj.nanjing.gov.cn/njssjj/214/224/index_18098.html
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://sjj.nanjing.gov.cn/njssjj/214/224/index_18098.html"
SOURCE_NAME = "南京市审计局_部门文件"
CATEGORY = "南京"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PAGE_SIZE = 20

_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'li', 'ul', 'ol', 'table', 'tr', 'td', 'th',
               'blockquote', 'pre', 'hr'}

# 所有CJK字符：基本区 + 扩展A区
_CJK_RANGE = r'[\u3400-\u4dbf\u4e00-\u9fff]'
# 中文标点符号
_CJK_PUNCT = r'[，。；：！？、""''（）《》【】—…·]'
# 所有CJK字符 + 中文标点
_CJK_OR_PUNCT = _CJK_RANGE + '|' + _CJK_PUNCT

# 横向空白字符集合：普通空格、制表符、不换行空格、全角空格
_H_WS = r'[ \t\u00a0\u3000]+'

# 预处理：将所有类型的空白统一为普通空格
_NORMALIZE_WS = re.compile(r'[ \t\u00a0\u3000]+')

# CJK字符之间的横向空白
_CJK_INNER_WS = re.compile(
    r'(?<=' + _CJK_RANGE + r')' + _H_WS + r'(?=' + _CJK_RANGE + r')'
)
# CJK字符 与 中文标点 之间的横向空白
_CJK_PUNCT_WS_BEFORE = re.compile(
    r'(?<=' + _CJK_RANGE + r')' + _H_WS + r'(?=' + _CJK_PUNCT + r')'
)
_CJK_PUNCT_WS_AFTER = re.compile(
    r'(?<=' + _CJK_PUNCT + r')' + _H_WS + r'(?=' + _CJK_RANGE + r')'
)
# 中文标点之间的横向空白
_PUNCT_PUNCT_WS = re.compile(
    r'(?<=' + _CJK_PUNCT + r')' + _H_WS + r'(?=' + _CJK_PUNCT + r')'
)
# 数字与中文单位之间的空白
_NUM_UNIT_WS = re.compile(r'(?<=\d)[ \t\u00a0\u3000]+(?=[年日月日条项章款式])')
# 3个及以上换行压缩
_MULTI_NL = re.compile(r'\n{3,}')


def _normalize_horizontal_ws(text):
    """将所有横向空白统一为普通空格"""
    return _NORMALIZE_WS.sub(' ', text)


def _strip_cjk_inner_ws(text):
    """删除段落内所有CJK相关的异常横向空白，不跨段落"""
    text = _CJK_INNER_WS.sub('', text)
    text = _CJK_PUNCT_WS_BEFORE.sub('', text)
    text = _CJK_PUNCT_WS_AFTER.sub('', text)
    text = _PUNCT_PUNCT_WS.sub('', text)
    text = _NUM_UNIT_WS.sub('', text)
    return text


def _clean_title(title):
    """清除标题中文字符之间的HTML排版空白"""
    text = _normalize_horizontal_ws(title)
    text = _strip_cjk_inner_ws(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _extract_text_preserve_blocks(content_elem):
    """
    遍历DOM树，按块级元素保留段落边界，行内元素拼接。

    - 块级元素(p/div/h1-h6/li/table等)：段落分隔
    - 行内元素(span/em/a/strong等)：直接拼接
    - <br>：段落分隔
    - 文本节点：逐段累积
    - 块级元素内部的直接文本在进入子块前先flush，避免与子块文本混淆
    """
    paragraphs = []
    current_parts = []

    def _flush():
        nonlocal current_parts
        if current_parts:
            text = ''.join(current_parts).strip()
            if text:
                paragraphs.append(text)
            current_parts.clear()

    def _walk(node):
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                current_parts.append(text)
            return

        if not isinstance(node, Tag) or not node.name:
            return

        tag_name = node.name.lower()

        if tag_name == 'br':
            _flush()
            return

        if tag_name in _BLOCK_TAGS:
            # 先flush块级元素之前累积的文本（父级直接文本）
            _flush()
            for child in node.children:
                _walk(child)
            # flush块级元素内部累积的文本
            _flush()
        else:
            for child in node.children:
                _walk(child)

    for child in content_elem.children:
        _walk(child)

    _flush()

    return '\n'.join(paragraphs)


def _clean_content(content):
    """对块级提取后的正文逐段清理横向空白，保留段落结构"""
    if not content:
        return content
    # 逐段清理，不跨段落删除换行
    paragraphs = content.split('\n')
    cleaned = []
    for para in paragraphs:
        para = _normalize_horizontal_ws(para)
        para = _strip_cjk_inner_ws(para)
        para = re.sub(r'[ \t]+', ' ', para)
        para = para.strip()
        if para:
            cleaned.append(para)
    return '\n'.join(cleaned)


def _extract_content(session, article_url, metrics):
    """抓取详情页正文内容"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        content_elem = (
            soup.select_one("div.con")
            or soup.select_one("div.view.TRS_UEDITOR")
            or soup.select_one("div.content")
            or soup.select_one(".wenZhang")
            or soup.select_one(".TRS_UEDITOR")
        )

        if content_elem:
            for extra in content_elem.select("script, style, table.info, table.t1"):
                extra.decompose()
            text = _extract_text_preserve_blocks(content_elem)
            return _clean_content(text)
        return ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取南京市审计局部门文件列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 0
    base_url = TARGET_URL.rsplit("/", 1)[0] + "/"

    try:
        while True:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{base_url}index_18098_{page_index}.html"

            resp = session.get(page_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.content, "html.parser")

            # 列表项选择器
            nodes = soup.select("#result > li")

            if not nodes:
                nodes = soup.select("ul.list li") or soup.select(".list li")

            if not nodes:
                break

            page_raw_count = len(nodes)
            metrics.raw_item_count += page_raw_count
            oldest_date_on_page = None

            for node in nodes:
                try:
                    # 标题：span.d1 a 或直接 a
                    link = node.select_one("span.d1 a") or node.select_one("a")
                    if not link:
                        continue

                    title = _clean_title(link.get_text(" ", strip=True))
                    href = (link.get("href") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    # 发布日期：span.d2 是发布日期（必须使用）
                    # 注意：span.d4 是废止日期，span.d5 是"是否有效"标记，均不能作为 pub_at
                    date_elem = node.select_one("span.d2")
                    pub_at = None

                    if date_elem:
                        date_text = date_elem.get_text(strip=True)
                        pub_at = parse_date(date_text)

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                        continue

                    article_url = urljoin(TARGET_URL, href)
                    metrics.valid_item_count += 1
                    latest_items.append({"title": title, "pub_at": pub_at})

                    # 记录页面最旧日期用于分页判断
                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    # 日期过滤
                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    # 抓取详情页内容
                    content = _extract_content(session, article_url, metrics)

                    policies.append({
                        "title": title,
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

            # 分页停止条件：最旧日期早于目标窗口起始
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
            # 如果当前页数量少于每页数量，说明是最后一页
            if page_raw_count < PAGE_SIZE:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )

    return policies, latest_items[:5], metrics


def run():
    """执行抓取并保存数据"""
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
