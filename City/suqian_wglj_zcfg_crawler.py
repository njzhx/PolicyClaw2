"""
宿迁市文化广电和旅游局_政策文件及解读爬虫
目标栏目：http://wgl.suqian.gov.cn/swhgd/zcfg/xxgk_list.shtml
列表结构：ul.listContent > li，每页16条，共3页约45条记录
正文：部分正文，部分图片政策解读，部分视频解读
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


HOST = "http://wgl.suqian.gov.cn"
TARGET_URL = HOST + "/swhgd/zcfg/xxgk_list.shtml"
SOURCE_NAME = "宿迁市文化广电和旅游局_政策文件及解读"
CATEGORY = "宿迁"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PAGE_SIZE = 16
MAX_PAGES = 100
_BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'li', 'ul', 'ol', 'table', 'tr', 'td', 'th',
               'blockquote', 'pre', 'hr'}
_ATTACHMENT_EXTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.wps'}
_GENERIC_ALT = {'', 'image', 'img', 'picture', 'photo', '图片', '图像', '照片', '图标', 'icon'}
_ICON_SRC_KEYWORDS = ('logo', 'icon', 'btn', 'button', 'arrow', 'close', 'search',
                      'qr', 'code', 'share', 'print', 'top', 'home', 'menu', 'bg',
                      'banner', 'ad-', '/ad/', 'weixin', 'wechat')


def _normalize_url(url, base_url):
    return urljoin(base_url, url)


def _strip_cjk_inner_ws(text):
    horizontal_ws = r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[，。；：！？、（）《》【】\u201c\u201d\u2018\u2019〔〕〈〉「」『』]"
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])", "", text)
    return text


def _normalize_horizontal_ws(text):
    return re.sub(r'[ \t\u00a0\u3000]+', ' ', text)


def _clean_title(title):
    text = _normalize_horizontal_ws(title)
    text = _strip_cjk_inner_ws(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _extract_text_preserve_blocks(content_elem):
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
    if not alt:
        return False
    alt_clean = alt.strip().lower()
    if alt_clean in _GENERIC_ALT:
        return False
    if len(alt_clean) < 2:
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


def _is_attachment_link(a_tag):
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
    for tag_name in ("script", "style", "noscript", "template"):
        for node in elem.find_all(tag_name):
            node.decompose()
    noise_texts = {"关闭本页", "打印本页", "返回顶部", "网站地图"}
    for node in list(elem.find_all(["a", "button"])):
        text = _clean_title(node.get_text(" ", strip=True))
        if text in noise_texts:
            node.decompose()
    return elem


def _truncate_functional_tail(text):
    if not text:
        return ""
    stop_markers = ("相关信息", "分享开始", "【收藏】")
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


def _extract_title_from_detail_page(soup, fallback_title, article_url=""):
    """从详情页提取真实标题。

    支持两种页面结构：
    - wgl.suqian.gov.cn：h1#title
    - www.suqian.gov.cn：表格中"名称"字段的 td 内容
    """
    # 方案1：wgl.suqian.gov.cn 的 h1#title
    h1 = soup.select_one("h1#title")
    if h1:
        return _clean_title(h1.get_text(" ", strip=True))

    # 方案2：www.suqian.gov.cn 的表格"名称"字段
    # 页面结构：table tr td("名称") + td(colspan=3, 包含完整标题)
    for td in soup.select("td"):
        text = td.get_text(" ", strip=True)
        # 找"名称"单元格
        if "名称" in text and len(text) < 30:
            parent = td.parent
            if parent and parent.name == "tr":
                tds = parent.find_all("td")
                for i, cell in enumerate(tds):
                    if cell.get_text(" ", strip=True).startswith("名称") and i < len(tds) - 1:
                        name_cell = tds[i + 1]
                        name_text = _clean_title(name_cell.get_text(" ", strip=True))
                        if name_text and len(name_text) > 5:
                            return name_text

    # 方案3：h2 标签（正文中的大标题，通常就是完整标题）
    for h2 in soup.select("h2"):
        text = _clean_title(h2.get_text(" ", strip=True))
        if text and len(text) > 5 and "来源" not in text and "索引号" not in text:
            return text

    # 方案4：title标签
    title_tag = soup.find("title")
    if title_tag:
        raw = _clean_title(title_tag.get_text(" ", strip=True))
        for suffix in (
            "-宿迁市文化广电和旅游局", " - 宿迁市文化广电和旅游局",
            " -宿迁市文化广电和旅游局", "_宿迁市文化广电和旅游局",
            "宿迁市文化广电和旅游局",
        ):
            if raw.endswith(suffix):
                return raw[: -len(suffix)].strip()
        for suffix in (
            "-宿迁市人民政府办公室", " - 宿迁市人民政府办公室",
            "宿迁市人民政府办公室",
        ):
            if raw.endswith(suffix):
                return raw[: -len(suffix)].strip()
        for suffix in (
            "-宿迁市人民政府", " - 宿迁市人民政府",
            "宿迁市人民政府",
        ):
            if raw.endswith(suffix):
                return raw[: -len(suffix)].strip()
        if raw.endswith("宿迁市文化广电和旅游局"):
            return raw[: -len("宿迁市文化广电和旅游局")].strip()
        return raw
    return fallback_title


def _extract_attachments_from_page(soup, article_url):
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
    if not attachments:
        return ""
    parts = []
    for name, url in attachments:
        parts.append(f"附件名称：{name}")
        parts.append(f"附件地址：{url}")
    return '\n'.join(parts)


def _extract_images(content_elem, article_url):
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
    parts = []
    for alt, url in images:
        if _is_meaningful_alt(alt):
            parts.append(f"图片说明：{alt.strip()}")
        parts.append(f"图片地址：{url}")
    return '\n'.join(parts)


def _extract_videos(content_elem, article_url):
    seen_urls = set()
    videos = []
    for video in content_elem.find_all('video'):
        src = (video.get('src') or '').strip()
        if src:
            absolute_url = _normalize_url(src, article_url)
            if absolute_url not in seen_urls:
                seen_urls.add(absolute_url)
                videos.append(absolute_url)
        for source in video.find_all('source'):
            src = (source.get('src') or '').strip()
            if src:
                absolute_url = _normalize_url(src, article_url)
                if absolute_url not in seen_urls:
                    seen_urls.add(absolute_url)
                    videos.append(absolute_url)
    return videos


def _build_video_content(videos):
    if not videos:
        return ""
    parts = []
    for url in videos:
        parts.append(f"视频地址：{url}")
    return '\n'.join(parts)


def _extract_iframes(content_elem, article_url):
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
    if not iframes:
        return ""
    parts = []
    for url in iframes:
        parts.append(f"嵌入地址：{url}")
    return '\n'.join(parts)


_ALLOWED_HOSTS = frozenset([
    "wgl.suqian.gov.cn",
    "www.suqian.gov.cn",
    "suqian.gov.cn",
])


def _is_valid_zcfg_link(href):
    if not href:
        return False

    href_lower = href.lower()
    excluded = (
        "mp.weixin.qq.com",
        "apply", "sqgk", "ysqgk", "login", "register",
        "javascript:", "mailto:", "tel:",
    )
    if any(p in href_lower for p in excluded):
        return False

    try:
        from urllib.parse import urljoin as _urljoin, urlparse as _urlparse

        absolute_href = _urljoin(TARGET_URL, href)
        hostname = (_urlparse(absolute_href).hostname or "").lower()
        target_hostname = (_urlparse(TARGET_URL).hostname or "").lower()

        allowed = {
            target_hostname,
            "suqian.gov.cn",
            "www.suqian.gov.cn",
        }
        return hostname in allowed
    except Exception:
        return False


def _remove_duplicate_title(text, title):
    if not text or not title:
        return text
    title_clean = title.strip()
    if text.strip().startswith(title_clean):
        remainder = text.strip()[len(title_clean):].strip()
        if remainder:
            return remainder
    return text


def _extract_content(session, article_url, metrics, title=""):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        detail_title = _extract_title_from_detail_page(soup, title, article_url)
        if detail_title and detail_title != title:
            title = detail_title

        # wgl.suqian.gov.cn 正文容器
        content_elem = (
            soup.select_one(".article-content")
            or soup.select_one("div.content")
            or soup.select_one("div.article")
        )
        # www.suqian.gov.cn 正文提取：
        # 结构：顶部元数据表格 → 正文标题 h2 → 段落/列表 → 相关信息
        # 策略：从正文标题 h2 开始，收集所有后续元素的文本直到"相关信息"
        if not content_elem:
            body = soup.find("body")
            if body:
                # 找正文标题 h2（跳过顶部元数据中的任何 h2）
                policy_h2 = None
                for h2 in body.find_all("h2"):
                    text = h2.get_text(" ", strip=True)
                    if text and len(text) > 5 and "相关信息" not in text:
                        # 确认是正文标题：不是来源、不是索引号
                        if "来源" not in text and "索引号" not in text:
                            policy_h2 = h2
                            break
                if policy_h2:
                    # 从 policy_h2 开始，收集所有后续兄弟节点直到"相关信息"区段
                    container = soup.new_tag("div")
                    for sibling in policy_h2.find_next_siblings():
                        if isinstance(sibling, NavigableString):
                            stripped = str(sibling).strip()
                            if stripped:
                                container.append(stripped)
                            continue
                        if not isinstance(sibling, Tag):
                            continue
                        text = sibling.get_text(" ", strip=True)
                        if "相关信息" in text:
                            break
                        # 跳过顶部元数据表格
                        if sibling.name == "table":
                            break
                        container.append(sibling)
                    if container.text.strip():
                        content_elem = container


        attachments = _extract_attachments_from_page(soup, article_url)

        if not content_elem:
            parts = []
            attachment_part = _build_attachment_content(attachments) if attachments else ""
            if attachment_part:
                parts.append(attachment_part)
            content = '\n'.join(parts)
            if not content:
                return "", detail_title
            if title:
                content = _remove_duplicate_title(content, title)
            return _clean_content(content), detail_title

        _remove_noise_elements(content_elem)

        text = _extract_text_preserve_blocks(content_elem)
        text = _clean_content(text)
        text = _truncate_functional_tail(text)
        text = _truncate_editor_tail(text)

        images = _extract_images(content_elem, article_url)
        videos = _extract_videos(content_elem, article_url)
        iframes = _extract_iframes(content_elem, article_url)

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
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    page_index = 0
    seen_urls = set()

    try:
        while page_index < MAX_PAGES:
            if page_index == 0:
                page_url = TARGET_URL
            else:
                page_url = f"{HOST}/swhgd/zcfg/xxgk_list_{page_index + 1}.shtml"

            resp = session.get(page_url, headers=HEADERS, timeout=30)
            if resp.status_code == 404 and page_index > 0:
                break
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.content, "html.parser")

            nodes = soup.select("ul.listContent > li")

            if not nodes:
                if page_index == 0:
                    metrics.errors.append("列表页未找到 ul.listContent > li")
                break

            page_raw_count = len(nodes)
            if page_index == 0:
                metrics.raw_item_count = page_raw_count
            else:
                metrics.raw_item_count += page_raw_count

            oldest_date_on_page = None
            page_new_count = 0

            for node in nodes:
                try:
                    link = node.select_one("a")
                    if not link:
                        metrics.invalid_item_count += 1
                        continue

                    href = (link.get("href") or "").strip()
                    if not _is_valid_zcfg_link(href):
                        # 非政策文件外链（微信等）不计入 raw/invalid，仅说明排除原因
                        metrics.raw_item_count -= 1
                        continue

                    list_title = _clean_title(
                        link.get_text(" ", strip=True) or link.get("title")
                    )
                    if not list_title or not href:
                        metrics.invalid_item_count += 1
                        continue

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
                    if article_url in seen_urls:
                        metrics.duplicate_policy_count += 1
                        continue
                    seen_urls.add(article_url)
                    page_new_count += 1
                    metrics.valid_item_count += 1

                    if oldest_date_on_page is None or pub_at < oldest_date_on_page:
                        oldest_date_on_page = pub_at

                    latest_items.append({"title": list_title, "pub_at": pub_at})

                    if not is_target_date(pub_at, target_from, target_to):
                        metrics.filtered_count += 1
                        continue

                    content, _ = _extract_content(
                        session, article_url, metrics, list_title
                    )

                    policies.append({
                        "title": list_title,
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

            if page_raw_count and page_new_count == 0:
                metrics.errors.append(
                    f"列表第{page_index + 1}页与已抓取页面重复，已停止翻页"
                )
                break
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
