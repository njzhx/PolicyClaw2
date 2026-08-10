"""
宿迁市医疗保障局_政策文件及解读爬虫
目标栏目：https://ybj.suqian.gov.cn/ybj/zcfg/xxgk_list.shtml
列表结构：ul.listContent > li，每个li包含a[href]链接和YYYY-MM-DD发布日期
正文容器：.article-content
支持：文字、正文图片、视频、附件
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


HOST = "https://ybj.suqian.gov.cn"
TARGET_URL = HOST + "/ybj/zcfg/xxgk_list.shtml"
BASE_URL = HOST + "/"
SOURCE_NAME = "宿迁市医疗保障局_政策文件及解读"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'li', 'ul', 'ol', 'table', 'tr', 'td', 'th',
               'blockquote', 'pre', 'hr'}

_ATTACHMENT_EXTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.wps'}

_GENERIC_ALT = {'', 'image', 'img', 'picture', 'photo', '图片', '图像', '照片', '图标', 'icon'}
_ICON_SRC_KEYWORDS = ('logo', 'icon', 'btn', 'button', 'arrow', 'close', 'search',
                      'qr', 'code', 'share', 'print', 'top', 'home', 'menu', 'bg',
                      'banner', 'ad-', '/ad/', 'weixin', 'wechat')


def _normalize_url(url, base_url):
    """将URL转换为绝对地址"""
    return urljoin(base_url, url)


def _strip_cjk_inner_ws(text):
    """删除HTML排版造成的中文横向空白，保留英文单词之间的正常空格。"""
    horizontal_ws = r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[，。；：！？、（）《》【】\u201c\u201d\u2018\u2019〔〕〈〉「」『』]"

    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])", "", text)
    return text


def _normalize_horizontal_ws(text):
    """将所有横向空白统一为普通空格"""
    return re.sub(r'[ \t\u00a0\u3000]+', ' ', text)


def _clean_title(title):
    """清除标题中文字符之间的HTML排版空白"""
    text = _normalize_horizontal_ws(title)
    text = _strip_cjk_inner_ws(text)
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


def _is_attachment_link(a_tag):
    """判断链接是否为真实附件"""
    href = (a_tag.get("href") or "").strip()
    if not href:
        return False
    lowered = href.lower()
    if lowered.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    if 'data:' in lowered:
        return False
    path = unquote(urlsplit(href).path).lower()
    return any(path.endswith(ext) for ext in _ATTACHMENT_EXTS)


def _remove_noise_elements(elem):
    """移除明确的非正文节点"""
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
    """从相关阅读或分享区开始截断"""
    if not text:
        return ""
    stop_markers = ("相关阅读", "分享开始", "【收藏】")
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
        kept.append(line)
    return "\n".join(kept)


def _truncate_editor_tail(text):
    """删除正文末尾的网站编辑信息"""
    if not text:
        return text
    search_start = int(len(text) * 0.85)
    markers = ("来源：", "编辑：", "校核：", "审签：", "来源:", "编辑:", "校核:", "审签:")
    positions = []
    for marker in markers:
        pos = text.find(marker, search_start)
        if pos >= 0:
            positions.append(pos)
    if positions:
        return text[:min(positions)].rstrip()
    return text


def _extract_title_from_detail_page(soup, fallback_title):
    """从详情页提取真实标题，删除末尾的机构名称"""
    title_tag = soup.find('title')
    if title_tag:
        raw_title = title_tag.get_text(" ", strip=True)
        raw_title = _clean_title(raw_title)
        # 删除末尾的机构名称
        for suffix in ("-宿迁市医疗保障局", " - 宿迁市医疗保障局", " -宿迁市医疗保障局",
                       "_宿迁市医疗保障局", "__宿迁市医疗保障局"):
            if raw_title.endswith(suffix):
                return raw_title[:-len(suffix)].strip()
        # 也处理没有分隔符的情况
        if raw_title.endswith("宿迁市医疗保障局"):
            return raw_title[:-len("宿迁市医疗保障局")].strip()
        return raw_title
    return fallback_title


def _extract_attachments(content_elem, article_url):
    """从正文容器中提取附件"""
    seen_urls = set()
    attachments = []
    for a_tag in content_elem.find_all("a", href=True):
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
    parts = []
    for name, url in attachments:
        parts.append(f"附件名称：{name}")
        parts.append(f"附件地址：{url}")
    return '\n'.join(parts)


def _extract_images(content_elem, article_url):
    """从正文容器中提取有效正文图片，返回 [(alt, url), ...]"""
    seen_urls = set()
    images = []
    for img in content_elem.find_all('img'):
        src = (img.get('src') or '').strip()
        if not src:
            continue
        if _is_icon_image(img):
            continue
        if src.startswith('data:'):
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


def _extract_videos(content_elem, article_url):
    """从正文容器中提取视频地址"""
    seen_urls = set()
    videos = []
    # 提取 video[src]
    for video in content_elem.find_all('video'):
        src = (video.get('src') or '').strip()
        if src:
            absolute_url = _normalize_url(src, article_url)
            if absolute_url not in seen_urls:
                seen_urls.add(absolute_url)
                videos.append(absolute_url)
    # 提取 video > source[src]
    for video in content_elem.find_all('video'):
        for source in video.find_all('source'):
            src = (source.get('src') or '').strip()
            if src:
                absolute_url = _normalize_url(src, article_url)
                if absolute_url not in seen_urls:
                    seen_urls.add(absolute_url)
                    videos.append(absolute_url)
    return videos


def _build_video_content(videos):
    """根据视频列表构建视频说明文本"""
    if not videos:
        return ""
    parts = []
    for url in videos:
        parts.append(f"视频地址：{url}")
    return '\n'.join(parts)


def _extract_iframes(content_elem, article_url):
    """从正文容器中提取iframe地址（用于嵌入视频）"""
    seen_urls = set()
    iframes = []
    for iframe in content_elem.find_all('iframe'):
        src = (iframe.get('src') or '').strip()
        if src and not src.startswith('about:'):
            absolute_url = _normalize_url(src, article_url)
            if absolute_url not in seen_urls:
                seen_urls.add(absolute_url)
                iframes.append(absolute_url)
    return iframes


def _build_iframe_content(iframes):
    """根据iframe列表构建嵌入内容说明文本"""
    if not iframes:
        return ""
    parts = []
    for url in iframes:
        parts.append(f"嵌入地址：{url}")
    return '\n'.join(parts)


def _is_valid_zcfg_link(href):
    """判断是否为有效的政策文件详情链接"""
    if not href:
        return False
    # 排除导航外链
    excluded_keywords = ('apply', 'sqgk', 'ysqgk', 'login', 'register')
    href_lower = href.lower()
    if any(kw in href_lower for kw in excluded_keywords):
        return False
    # 只接受 /ybj/zcfg/ 详情链接
    return '/ybj/zcfg/' in href


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
    """抓取详情页正文"""
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        # 优先从详情页提取真实标题
        detail_title = _extract_title_from_detail_page(soup, title)
        if detail_title and detail_title != title:
            title = detail_title

        content_elem = soup.select_one(".article-content")

        if not content_elem:
            # 如果没有正文容器，仍返回详情页真实标题
            return "", detail_title

        _remove_noise_elements(content_elem)

        # 提取正文文本
        text = _extract_text_preserve_blocks(content_elem)
        text = _clean_content(text)
        text = _truncate_functional_tail(text)
        text = _truncate_editor_tail(text)

        # 提取正文图片
        images = _extract_images(content_elem, article_url)

        # 提取视频
        videos = _extract_videos(content_elem, article_url)

        # 提取iframe嵌入
        iframes = _extract_iframes(content_elem, article_url)

        # 提取附件（从正文容器内）
        attachments = _extract_attachments(content_elem, article_url)

        # 构建最终正文
        parts = []
        text_part = text.strip() if text else ""
        if text_part:
            parts.append(text_part)

        image_part = _build_image_content(images) if images else ""
        if image_part:
            parts.append(image_part)

        video_part = _build_video_content(videos) if videos else ""
        if video_part:
            parts.append(video_part)

        iframe_part = _build_iframe_content(iframes) if iframes else ""
        if iframe_part:
            parts.append(iframe_part)

        attachment_part = _build_attachment_content(attachments) if attachments else ""
        if attachment_part:
            parts.append(attachment_part)

        content = '\n'.join(parts)

        if title:
            content = _remove_duplicate_title(content, title)

        content = _clean_content(content)

        return content, detail_title
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return "", title


def scrape_data():
    """抓取宿迁市医疗保障局政策文件及解读列表"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()

    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    try:
        resp = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.content, "html.parser")

        nodes = soup.select("ul.listContent > li")

        if not nodes:
            metrics.errors.append("列表页未找到 ul.listContent > li")
            return policies, latest_items, metrics

        metrics.raw_item_count = len(nodes)

        for node in nodes:
            try:
                link = node.select_one("a")
                if not link:
                    metrics.invalid_item_count += 1
                    continue

                href = (link.get("href") or "").strip()

                # 过滤非政策文件链接
                if not _is_valid_zcfg_link(href):
                    metrics.invalid_item_count += 1
                    continue

                # 从列表标题（可能被截断）
                list_title = _clean_title(link.get_text(" ", strip=True))
                if not list_title or not href:
                    metrics.invalid_item_count += 1
                    continue

                # 从li文本中提取日期 YYYY-MM-DD
                full_text = node.get_text(" ", strip=True)
                date_pattern = re.search(r'(\d{4}-\d{2}-\d{2})', full_text)
                pub_at = None
                if date_pattern:
                    pub_at = parse_date(date_pattern.group(1))

                if not pub_at:
                    metrics.invalid_item_count += 1
                    metrics.errors.append(f"无法解析发布日期: {list_title[:30]}...")
                    continue

                article_url = _normalize_url(href, TARGET_URL)
                metrics.valid_item_count += 1
                latest_items.append({"title": list_title, "pub_at": pub_at})

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                # 详情页会重新获取真实标题
                content, final_title = _extract_content(session, article_url, metrics, list_title)
                final_title = final_title or list_title
                latest_items[-1]["title"] = final_title

                policies.append({
                    "title": final_title,
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
