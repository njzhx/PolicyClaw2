"""
南京市人民政府外事办公室_部门文件爬虫
目标栏目：https://wb.nanjing.gov.cn/njszfwsbgs/214/224/index_18100.html
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


TARGET_URL = "https://wb.nanjing.gov.cn/njszfwsbgs/214/224/index_18100.html"
SOURCE_NAME = "南京市人民政府外事办公室_部门文件"
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

# CJK字符范围：基本区 + 扩展A区
_CJK_RANGE = r'[\u3400-\u4dbf\u4e00-\u9fff]'
# 中文标点
_CJK_PUNCT = r'[，。；：！？、""''（）《》【】—…·]'
# 横向空白：空格、制表符、不换行空格、全角空格
_H_WS = r'[ \t\u00a0\u3000]+'

_NORMALIZE_WS = re.compile(r'[ \t\u00a0\u3000]+')
_CJK_INNER_WS = re.compile(
    r'(?<=' + _CJK_RANGE + r')' + _H_WS + r'(?=' + _CJK_RANGE + r')'
)
_CJK_PUNCT_WS_BEFORE = re.compile(
    r'(?<=' + _CJK_RANGE + r')' + _H_WS + r'(?=' + _CJK_PUNCT + r')'
)
_CJK_PUNCT_WS_AFTER = re.compile(
    r'(?<=' + _CJK_PUNCT + r')' + _H_WS + r'(?=' + _CJK_RANGE + r')'
)
_PUNCT_PUNCT_WS = re.compile(
    r'(?<=' + _CJK_PUNCT + r')' + _H_WS + r'(?=' + _CJK_PUNCT + r')'
)
_NUM_UNIT_WS = re.compile(r'(?<=\d)[ \t\u00a0\u3000]+(?=[年日月日条项章款式])')


def _normalize_horizontal_ws(text):
    """将所有横向空白统一为普通空格"""
    return _NORMALIZE_WS.sub(' ', text)


def _strip_cjk_inner_ws(text):
    """删除段落内所有CJK相关的异常横向空白"""
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
    - 块级元素内部的直接文本在进入子块前先flush，避免父子重复提取
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
            _flush()
            for child in node.children:
                _walk(child)
            _flush()
        else:
            for child in node.children:
                _walk(child)

    for child in content_elem.children:
        _walk(child)

    _flush()

    return '\n'.join(paragraphs)


def _clean_content(content):
    """对正文逐段清理横向空白，保留段落结构"""
    if not content:
        return content
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


# 无信息量的通用 alt 文本
_GENERIC_ALT = {'', 'image', 'img', 'picture', 'photo', '图片', '图像', '照片', '图标', 'icon'}
# 图片 src 中常见的非正文图标关键词
_ICON_SRC_KEYWORDS = ('logo', 'icon', 'btn', 'button', 'arrow', 'close', 'search',
                       'qr', 'code', 'share', 'print', 'top', 'home', 'menu', 'bg',
                       'banner', 'ad-', '/ad/')


def _is_meaningful_alt(alt):
    """判断 alt 文本是否有意义"""
    if not alt:
        return False
    alt_clean = alt.strip().lower()
    if alt_clean in _GENERIC_ALT:
        return False
    if len(alt_clean) < 2:
        return False
    return True


def _is_icon_image(img_tag):
    """根据 src 和属性判断是否为图标类图片"""
    src = (img_tag.get('src') or '').lower()
    if any(kw in src for kw in _ICON_SRC_KEYWORDS):
        return True
    # 检查 width/height 属性是否过小
    for attr in ('width', 'height'):
        val = img_tag.get(attr)
        if val:
            try:
                if int(str(val).replace('px', '')) < 50:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def _normalize_image_url(src, article_url):
    """将图片相对地址转为绝对地址，wb.nanjing.gov.cn 统一 https"""
    absolute = urljoin(article_url, src)
    if absolute.startswith('http://') and 'wb.nanjing.gov.cn' in absolute:
        absolute = 'https://' + absolute[len('http://'):]
    return absolute


def _extract_images(content_elem, article_url):
    """
    从正文容器中提取有效正文图片。

    返回 [(alt, url), ...] 列表，已去重。
    """
    seen_urls = set()
    images = []

    for img in content_elem.find_all('img'):
        src = (img.get('src') or '').strip()
        if not src:
            continue

        if _is_icon_image(img):
            continue

        absolute_url = _normalize_image_url(src, article_url)
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        alt = img.get('alt') or img.get('title') or ''
        images.append((alt, absolute_url))

    return images


def _build_image_content(images):
    """根据图片列表构建图片说明文本"""
    parts = []
    for alt, url in images:
        if _is_meaningful_alt(alt):
            parts.append(f"图片说明：{alt.strip()}")
        parts.append(f"图片地址：{url}")
    return '\n'.join(parts)


def _remove_duplicate_title(text, title):
    """确保标题在正文中最多出现一次（移除开头的重复标题）"""
    if not text or not title:
        return text
    title_clean = title.strip()
    if text.strip().startswith(title_clean):
        remainder = text.strip()[len(title_clean):].strip()
        if remainder:
            return remainder
    return text


def _extract_content(session, article_url, metrics, title=""):
    """抓取详情页正文内容，支持普通正文页和图片型页面"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 南京市政府子站通用正文容器
        content_elem = (
            soup.select_one("div.con")
            or soup.select_one("div.view.TRS_UEDITOR")
            or soup.select_one("div.content")
            or soup.select_one(".wenZhang")
            or soup.select_one(".TRS_UEDITOR")
        )

        if not content_elem:
            return ""

        # 移除脚本、样式、元数据表格
        for extra in content_elem.select("script, style, table.info, table.t1"):
            extra.decompose()

        # 提取正文文本
        text = _extract_text_preserve_blocks(content_elem)
        text = _clean_content(text)

        # 提取正文图片
        images = _extract_images(content_elem, article_url)

        # 组合文本和图片
        content = _compose_content(text, images)

        # 确保标题在正文中最多出现一次
        if title:
            content = _remove_duplicate_title(content, title)

        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _compose_content(text, images):
    """组合正文文本和图片信息"""
    text_part = text.strip() if text else ""
    image_part = _build_image_content(images) if images else ""

    if text_part and image_part:
        return f"{text_part}\n{image_part}"
    if image_part:
        return image_part
    return text_part


def scrape_data():
    """抓取南京市人民政府外事办公室部门文件列表"""
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
                page_url = f"{base_url}index_18100_{page_index}.html"

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
                    # 标题：span.d1 a
                    link = node.select_one("span.d1 a") or node.select_one("a")
                    if not link:
                        continue

                    title = _clean_title(link.get_text(" ", strip=True))
                    href = (link.get("href") or "").strip()

                    if not title or not href:
                        metrics.invalid_item_count += 1
                        continue

                    # 发布日期：span.d2 是列表页发布日期
                    # span.d4 是废止日期，span.d5 是"是否有效"标记，均不能作为 pub_at
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

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    content = _extract_content(session, article_url, metrics, title)

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

            # 分页停止条件
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break
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
