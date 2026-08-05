"""
南京市统计局_部门文件爬虫
目标栏目：https://tjj.nanjing.gov.cn/njstjj/214/224/index_18111.html
"""
import re
from urllib.parse import urljoin, unquote, urlsplit

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


TARGET_URL = "https://tjj.nanjing.gov.cn/njstjj/214/224/index_18111.html"
SOURCE_NAME = "南京市统计局_部门文件"
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

# 附件格式白名单
_ATTACHMENT_EXTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.wps'}
# 相关阅读/政策解读/图解等关键词，需排除
_RELATED_KEYWORDS = ('解读', '图解', '相关阅读', '政策解读')

# 无信息量的通用 alt 文本
_GENERIC_ALT = {'', 'image', 'img', 'picture', 'photo', '图片', '图像', '照片', '图标', 'icon'}
# 图片 src 中常见的非正文图标关键词
_ICON_SRC_KEYWORDS = ('logo', 'icon', 'btn', 'button', 'arrow', 'close', 'search',
                       'qr', 'code', 'share', 'print', 'top', 'home', 'menu', 'bg',
                       'banner', 'ad-', '/ad/')


def _normalize_url(url, base_url):
    """将URL转换为绝对地址，tjj.nanjing.gov.cn 统一使用 https"""
    absolute = urljoin(base_url, url)
    if absolute.startswith('http://') and 'tjj.nanjing.gov.cn' in absolute:
        absolute = 'https://' + absolute[len('http://'):]
    return absolute


def _strip_cjk_inner_ws(text):
    """删除HTML排版造成的中文横向空白，保留英文单词之间的正常空格。"""
    horizontal_ws = (
        r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    )
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[，。；：！？、（）《》【】\u201c\u201d\u2018\u2019〔〕〈〉「」『』]"

    # 中文字符之间的排版空格
    text = re.sub(
        rf"(?<={cjk}){horizontal_ws}(?={cjk})",
        "",
        text,
    )

    # 中文字符与中文标点之间的排版空格
    text = re.sub(
        rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})",
        "",
        text,
    )
    text = re.sub(
        rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})",
        "",
        text,
    )

    # 数字与年月日、条款等单位之间的排版空格
    text = re.sub(
        rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])",
        "",
        text,
    )

    # 邮箱地址中@前后的排版空格
    text = re.sub(
        rf"(?<=[A-Za-z0-9._%+-]){horizontal_ws}(?=@)",
        "",
        text,
    )
    text = re.sub(
        rf"(?<=@){horizontal_ws}(?=[A-Za-z0-9.-])",
        "",
        text,
    )

    return text


def _normalize_horizontal_ws(text):
    """将所有横向空白统一为普通空格"""
    return re.sub(r'[ \t\u00a0\u3000]+', ' ', text)


def _clean_title(title):
    """清除标题中文字符之间的HTML排版空白"""
    text = _normalize_horizontal_ws(title)
    text = _strip_cjk_inner_ws(text)
    text = re.sub(
        r'(?<=[\u3400-\u4dbf\u4e00-\u9fff])[ \t\u00a0\u3000]+(?=[\u201c\u201d\u2018\u2019《》【】（）])',
        '',
        text,
    )
    text = re.sub(
        r'(?<=[\u201c\u201d\u2018\u2019《》【】（）])[ \t\u00a0\u3000]+(?=[\u3400-\u4dbf\u4e00-\u9fff])',
        '',
        text,
    )
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _extract_text_preserve_blocks(content_elem):
    """
    遍历DOM树，按块级元素保留段落边界，行内元素拼接。
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
    for attr in ('width', 'height'):
        val = img_tag.get(attr)
        if val:
            try:
                if int(str(val).replace('px', '')) < 50:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def _is_related_link(a_tag):
    """判断链接是否为相关阅读/政策解读/图解等"""
    text = a_tag.get_text(strip=True)
    href = a_tag.get('href') or ''
    for keyword in _RELATED_KEYWORDS:
        if keyword in text or keyword in href:
            return True
    return False


def _is_attachment_link(a_tag):
    """判断链接是否为真实附件，并排除解读、图解等普通页面链接。"""
    if _is_related_link(a_tag):
        return False
    href = (a_tag.get("href") or "").strip()
    if not href:
        return False
    lowered = href.lower()
    if lowered.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    path = unquote(urlsplit(href).path).lower()
    return any(path.endswith(ext) for ext in _ATTACHMENT_EXTS)


def _remove_noise_elements(elem):
    """只移除明确的非正文节点，禁止按祖先容器全文执行decompose。"""
    for tag_name in ("script", "style", "noscript", "template", "iframe"):
        for node in elem.find_all(tag_name):
            node.decompose()
    noise_texts = {"关闭本页", "打印本页", "返回顶部", "网站地图"}
    for node in list(elem.find_all(["a", "button"])):
        text = _clean_title(node.get_text(" ", strip=True))
        if text in noise_texts:
            node.decompose()
    return elem


def _truncate_functional_tail(text):
    """从相关阅读或分享区开始截断，仅处理已经提取的正文文本。"""
    if not text:
        return ""
    stop_markers = ("相关阅读", "分享开始")
    script_prefixes = ("var fxtitle", "var fxurl", "var fxdesc")
    exact_noise = {"关闭本页", "打印本页", "返回顶部", "网站地图", "end"}
    kept = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        compact = re.sub(r"[ \t\u00a0\u3000]+", "", line)
        cut_positions = [
            compact.find(marker) for marker in stop_markers if marker in compact
        ]
        if cut_positions:
            cut_at = min(cut_positions)
            prefix = compact[:cut_at].strip()
            if prefix:
                kept.append(prefix)
            break
        lowered = compact.lower()
        if any(lowered.startswith(prefix) for prefix in script_prefixes):
            continue
        if compact in exact_noise:
            continue
        kept.append(line)
    return "\n".join(kept)


def _extract_attachments(soup, article_url):
    """从整页所有真实文件链接中提取附件，并按绝对URL去重。"""
    seen_urls = set()
    attachments = []
    for a_tag in soup.find_all("a", href=True):
        if not _is_attachment_link(a_tag):
            continue
        href = (a_tag.get("href") or "").strip()
        absolute_url = _normalize_url(href, article_url)
        if absolute_url in seen_urls:
            continue
        name = _clean_title(a_tag.get_text(" ", strip=True))
        if not name:
            name = unquote(urlsplit(absolute_url).path.rsplit("/", 1)[-1])
        if not name:
            continue
        seen_urls.add(absolute_url)
        attachments.append((name, absolute_url))
    return attachments


def _build_attachment_content(attachments):
    """根据附件列表构建附件说明文本"""
    if not attachments:
        return ""
    parts = ["附件："]
    for name, url in attachments:
        parts.append(f"附件名称：{name}")
        parts.append(f"附件地址：{url}")
    return '\n'.join(parts)


def _extract_images(content_elem, article_url):
    """从正文容器中提取有效正文图片，返回 [(alt, url), ...] 列表，已去重。"""
    seen_urls = set()
    images = []

    for img in content_elem.find_all('img'):
        src = (img.get('src') or '').strip()
        if not src:
            continue

        if _is_icon_image(img):
            continue

        absolute_url = _normalize_url(src, article_url)
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
    """确保标题在正文中最多出现一次"""
    if not text or not title:
        return text
    title_clean = title.strip()
    if text.strip().startswith(title_clean):
        remainder = text.strip()[len(title_clean):].strip()
        if remainder:
            return remainder
    return text


def _extract_content(session, article_url, metrics, title=""):
    """抓取详情页正文，支持普通正文页、附件型页面和图片型页面。"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 必须先从完整页面提取附件，避免正文清理误删文件下载区域。
        attachments = _extract_attachments(soup, article_url)

        content_elem = (
            soup.select_one("div.con")
            or soup.select_one("div.view.TRS_UEDITOR")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".wenZhang")
            or soup.select_one("div.content")
        )
        if not content_elem:
            return _build_attachment_content(attachments) if attachments else ""

        _remove_noise_elements(content_elem)

        for selector in ("table.info", "table.t1"):
            for node in content_elem.select(selector):
                node.decompose()

        text = _extract_text_preserve_blocks(content_elem)
        text = _clean_content(text)
        text = _truncate_functional_tail(text)

        images = _extract_images(content_elem, article_url)

        # 组合正文、附件和图片
        parts = []
        text_part = text.strip() if text else ""
        if text_part:
            parts.append(text_part)

        # 如果正文只有"详见文件下载"且有附件，用附件内容替代
        if text_part in ("详见文件下载", "详见附件", "详见文件", "") and attachments:
            attachment_part = _build_attachment_content(attachments)
            if attachment_part:
                parts.append(attachment_part)
        elif attachments:
            attachment_part = _build_attachment_content(attachments)
            if attachment_part:
                parts.append(attachment_part)

        image_part = _build_image_content(images) if images else ""
        if image_part:
            parts.append(image_part)

        content = '\n'.join(parts)

        if title:
            content = _remove_duplicate_title(content, title)

        # 对最终组装完成的正文、附件名称和说明统一再清理一次
        content = _clean_content(content)

        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    """抓取南京市统计局部门文件列表"""
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
                page_url = f"{base_url}index_18111_{page_index}.html"

            resp = session.get(page_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.content, "html.parser")

            # 列表项选择器
            nodes = soup.select("li")

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

                    # 发布日期：span.d2 是列表页发布日期
                    # span.d4 是废止日期，span.d5 是"是否有效"标记（"否"），均不能作为 pub_at
                    date_elem = node.select_one("span.d2")
                    pub_at = None

                    if date_elem:
                        date_text = date_elem.get_text(strip=True)
                        pub_at = parse_date(date_text)

                    if not pub_at:
                        metrics.invalid_item_count += 1
                        metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                        continue

                    article_url = _normalize_url(href, TARGET_URL)
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
