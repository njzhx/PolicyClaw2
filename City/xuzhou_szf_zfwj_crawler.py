"""
徐州市人民政府_市政府文件爬虫
目标栏目：https://www.xz.gov.cn/dynamic/zwgk/govInfoPubright.html?categorynum=003001003
列表接口：https://www.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist
"""
import json
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


TARGET_URL = "https://www.xz.gov.cn/dynamic/zwgk/govInfoPubright.html?categorynum=003001003"
SOURCE_NAME = "徐州市人民政府_市政府文件"
CATEGORY = "徐州"

LIST_API_URL = "https://www.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist"
SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
CATEGORY_NUM = "003001003"
PAGE_SIZE = 20

HEADERS = {
    "Content-Type": "application/json",
    "Referer": TARGET_URL,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'li', 'ul', 'ol', 'table', 'tr', 'td', 'th', 'tbody', 'thead',
               'blockquote', 'pre', 'hr'}

_ATTACHMENT_EXTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.wps'}
_RELATED_KEYWORDS = ('解读', '图解', '相关阅读', '政策解读')
_INTERPRETATION_PREFIXES = (
    '政策解读：', '政策解读:', '解读文件：', '解读文件:',
    '相关解读：', '相关解读:',
)

_GENERIC_ALT = {'', 'image', 'img', 'picture', 'photo', '图片', '图像', '照片', '图标', 'icon'}
_ICON_SRC_KEYWORDS = ('logo', 'icon', 'btn', 'button', 'arrow', 'close', 'search',
                       'qr', 'code', 'share', 'print', 'top', 'home', 'menu', 'bg',
                       'banner', 'ad-', '/ad/')

# 最大页数安全上限
MAX_PAGES = 100


def _normalize_url(url, base_url):
    """将URL转换为绝对地址"""
    if not url:
        return url
    absolute = urljoin(base_url, url)
    return absolute


def _strip_cjk_inner_ws(text):
    """删除HTML排版造成的中文横向空白"""
    horizontal_ws = r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[，。；：！？、？！；：、…—·《》【】（）〔〕〈〉「」『』""'']"

    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])", "", text)
    text = re.sub(rf"(?<=[A-Za-z0-9._%+-]){horizontal_ws}(?=@)", "", text)
    text = re.sub(rf"(?<=@){horizontal_ws}(?=[A-Za-z0-9.-])", "", text)
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
        '', text)
    text = re.sub(
        r'(?<=[\u201c\u201d\u2018\u2019《》【】（）])[ \t\u00a0\u3000]+(?=[\u3400-\u4dbf\u4e00-\u9fff])',
        '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _extract_text_preserve_blocks(content_elem):
    """遍历DOM树，按块级元素保留段落边界"""
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
            if node.strip():
                current_parts.append(str(node))
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
    """对正文逐段清理横向空白"""
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

    # 删除末尾的空附件下载和视频标签
    EMPTY_LABELS = {"附件下载：", "附件下载:", "视频：", "视频:"}
    while cleaned:
        last = cleaned[-1].strip()
        if last in EMPTY_LABELS:
            cleaned.pop()
        elif not last:
            cleaned.pop()
        else:
            break

    return '\n'.join(cleaned)


def _is_meaningful_alt(alt):
    if not alt:
        return False
    alt_clean = alt.strip().lower()
    if alt_clean in _GENERIC_ALT or len(alt_clean) < 2:
        return False
    return True


def _is_icon_image(img_tag):
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
    text = a_tag.get_text(strip=True)
    href = a_tag.get('href') or ''
    for keyword in _RELATED_KEYWORDS:
        if keyword in text or keyword in href:
            return True
    return False


def _is_attachment_link(a_tag):
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
    """移除非正文节点"""
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
    """从相关阅读或政策解读入口开始截断"""
    if not text:
        return ""
    stop_markers = ("相关阅读", "分享开始")
    exact_noise = {"关闭本页", "打印本页", "返回顶部", "网站地图", "end"}
    kept = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        compact = re.sub(r"[ \t\u00a0\u3000]+", "", line)
        cut_positions = [compact.find(m) for m in stop_markers if m in compact]
        if cut_positions:
            cut_at = min(cut_positions)
            prefix = compact[:cut_at].strip()
            if prefix:
                kept.append(prefix)
            break
        lowered = compact.lower()
        if any(lowered.startswith(p) for p in ("var fx",)):
            continue
        if compact in exact_noise:
            continue
        if any(compact.startswith(p) for p in _INTERPRETATION_PREFIXES):
            continue
        kept.append(line)
    return "\n".join(kept)


def _normalize_for_comparison(text):
    if not text:
        return ""
    return re.sub(r'[\s\u00a0\u3000]+', '', text).strip()


def _extract_attachments(soup, article_url):
    """从整页所有真实文件链接中提取附件"""
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
        name = _strip_cjk_inner_ws(name)
        seen_urls.add(absolute_url)
        attachments.append((name, absolute_url))
    return attachments


def _remove_standalone_attachment_links(elem, attachment_urls):
    """移除正文区域内仅包含附件链接文字的孤立段落"""
    attachment_names = set()
    for p_tag in elem.find_all('p'):
        p_text = _clean_title(p_tag.get_text(" ", strip=True))
        if p_text and p_text in attachment_names:
            p_tag.decompose()
            continue
        a_tags = p_tag.find_all('a', href=True)
        if len(a_tags) == 1:
            a_tag = a_tags[0]
            a_href = (a_tag.get('href') or "").strip()
            a_text = _clean_title(a_tag.get_text(" ", strip=True))
            a_abs_url = _normalize_url(a_href, "")
            if a_abs_url in attachment_urls and a_text:
                attachment_names.add(a_text)
                p_tag.decompose()
    return elem


def _remove_duplicate_attachment_names(text, attachments):
    """删除正文中独立成行且与附件名称完全相同的文本行"""
    if not text or not attachments:
        return text
    attachment_names_normalized = {_normalize_for_comparison(name) for name, _ in attachments}
    kept_lines = []
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        line_normalized = _normalize_for_comparison(line_stripped)
        if line_normalized and line_normalized in attachment_names_normalized:
            continue
        kept_lines.append(line)
    return '\n'.join(kept_lines)


def _build_attachment_content(attachments):
    if not attachments:
        return ""
    parts = ["附件："]
    for name, url in attachments:
        parts.append(f"附件名称：{name}")
        parts.append(f"附件地址：{url}")
    return '\n'.join(parts)


def _extract_images(content_elem, article_url):
    """从正文容器中提取有效图片"""
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
    parts = []
    for alt, url in images:
        if _is_meaningful_alt(alt):
            parts.append(f"图片说明：{alt.strip()}")
        parts.append(f"图片地址：{url}")
    return '\n'.join(parts)


def _remove_duplicate_title(text, title):
    if not text or not title:
        return text
    title_clean = title.strip()
    if text.strip().startswith(title_clean):
        remainder = text.strip()[len(title_clean):].strip()
        if remainder:
            return remainder
    return text


def _is_error_page(soup):
    """判断是否为错误页面"""
    # 检查页面标题
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        if '您访问的页面不存在' in title_text:
            return True
        if '页面不存在' in title_text and '404' in title_text:
            return True

    # 检查页面全文长度
    body_text = soup.get_text()
    if len(body_text.strip()) < 100:
        return True

    return False


def _remove_empty_attachment_labels(elem):
    """删除空的附件下载和视频标签"""
    # 删除空的"附件下载："标签
    for tag in elem.find_all(['div', 'p', 'span']):
        text = tag.get_text(strip=True)
        if text in ('附件下载：', '附件下载:', '附件下载', '视频：', '视频:', '视频'):
            # 检查该标签内是否有有效内容（排除空白）
            inner = tag.decode_contents()
            inner_clean = re.sub(r'<[^>]+>', '', inner).strip()
            if not inner_clean or inner_clean == '':
                tag.decompose()


def _extract_content(session, article_url, metrics, title=""):
    """抓取详情页正文"""
    try:
        response = session.get(article_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": TARGET_URL,
        }, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 检查是否为错误页面
        if _is_error_page(soup):
            metrics.errors.append(f"详情页为错误页面: {article_url}")
            return ""

        # 先提取附件
        attachments = _extract_attachments(soup, article_url)

        # 优先使用 .mian-cont 作为正文容器
        content_elem = (
            soup.select_one("div.mian-cont")
            or soup.select_one(".mian-cont")
            or soup.select_one("div.con")
            or soup.select_one("div.view.TRS_UEDITOR")
            or soup.select_one(".TRS_UEDITOR")
            or soup.select_one(".wenZhang")
            or soup.select_one("div.content")
            or soup.select_one("div.article")
            or soup.select_one("div.detail")
        )
        if not content_elem:
            return _build_attachment_content(attachments) if attachments else ""

        _remove_noise_elements(content_elem)

        for selector in ("table.info", "table.t1", "table.metadata"):
            for node in content_elem.select(selector):
                node.decompose()

        # 删除空的附件下载和视频标签
        _remove_empty_attachment_labels(content_elem)

        # 移除孤立附件链接段落
        attachment_urls = {url for _, url in attachments}
        _remove_standalone_attachment_links(content_elem, attachment_urls)

        text = _extract_text_preserve_blocks(content_elem)
        text = _clean_content(text)
        text = _truncate_functional_tail(text)

        if attachments:
            text = _remove_duplicate_attachment_names(text, attachments)

        images = _extract_images(content_elem, article_url)

        parts = []
        text_part = text.strip() if text else ""
        if text_part:
            parts.append(text_part)

        if attachments:
            attachment_part = _build_attachment_content(attachments)
            if attachment_part:
                parts.append(attachment_part)

        image_part = _build_image_content(images) if images else ""
        if image_part:
            parts.append(image_part)

        content = '\n'.join(parts)

        if title:
            content = _remove_duplicate_title(content, title)

        content = _clean_content(content)

        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _fetch_list_page(session, page_index):
    """请求列表API某一页"""
    payload = {
        "deptcode": "",
        "categorynum": CATEGORY_NUM,
        "pageIndex": page_index,
        "pageSize": PAGE_SIZE,
        "siteGuid": SITE_GUID,
    }
    resp = session.post(LIST_API_URL, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # 优先使用 response.json()，失败时使用 json.loads
    try:
        return resp.json()
    except json.JSONDecodeError:
        return json.loads(resp.text)


def _parse_list_response(data):
    """
    解析API响应，返回 (records, total_count, has_more)
    真实路径：data["custom"]["data"], data["custom"]["total"]
    """
    if not isinstance(data, dict):
        return [], None, False

    custom = data.get("custom")
    if not isinstance(custom, dict):
        return [], None, False

    records = custom.get("data")
    if not isinstance(records, list):
        return [], None, False

    total = custom.get("total")
    if total is not None:
        try:
            total = int(total)
        except (ValueError, TypeError):
            total = None

    return records, total, True


def _extract_record_info(record):
    """
    从单条记录中提取标题、URL和发布日期
    真实字段：title, infourl, visiturl, linkurl, infodate, handsdate
    """
    # 标题：优先使用 title
    title = record.get('title') or record.get('realtitle') or ""

    # 详情URL：优先 infourl，其次 visiturl，最后 linkurl
    detail_url = (
        record.get('infourl')
        or record.get('visiturl')
        or record.get('linkurl')
        or ""
    )

    # 发布日期：优先 infodate，其次 handsdate
    pub_date_str = record.get('infodate') or record.get('handsdate') or ""

    return {
        'title': title,
        'detail_url': detail_url,
        'pub_date_str': pub_date_str,
    }


def scrape_data():
    """抓取徐州市人民政府市政府文件列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 0
    total_records = None
    oldest_date_on_page = None

    try:
        while page_index < MAX_PAGES:
            data = _fetch_list_page(session, page_index)
            records, total, has_more = _parse_list_response(data)

            if not records:
                break

            if page_index == 0 and total:
                total_records = total
                print(f"[DEBUG] 总记录数: {total_records}")

            metrics.raw_item_count += len(records)

            for record in records:
                info = _extract_record_info(record)

                title = _clean_title(info['title'])
                detail_url = info['detail_url']
                pub_date_str = info['pub_date_str']

                if not title or not detail_url:
                    metrics.invalid_item_count += 1
                    continue

                # 转换详情URL为绝对地址
                if not detail_url.startswith('http'):
                    detail_url = urljoin("https://www.xz.gov.cn/", detail_url.lstrip('/'))

                # 解析发布日期
                pub_at = None
                if pub_date_str:
                    pub_at = parse_date(pub_date_str)

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {title[:30]}...")
                    continue

                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                    oldest_date_on_page = pub_at

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                content = _extract_content(session, detail_url, metrics, title)

                policies.append({
                    "title": title,
                    "url": detail_url,
                    "pub_at": pub_at,
                    "content": content,
                    "selected": False,
                    "category": CATEGORY,
                    "source": SOURCE_NAME,
                })

            # 分页停止条件
            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            # 如果没有更多数据，停止
            if not has_more or len(records) < PAGE_SIZE:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

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
