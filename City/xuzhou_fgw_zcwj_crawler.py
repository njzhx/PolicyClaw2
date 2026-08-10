"""
徐州市发展和改革委员会_政策文件爬虫
目标栏目：https://dpc.xz.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003006
列表接口：https://dpc.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist
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


TARGET_URL = "https://dpc.xz.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003006"
SOURCE_NAME = "徐州市发展和改革委员会_政策文件"
CATEGORY = "徐州"
BASE_URL = "https://dpc.xz.gov.cn/"

LIST_API_URL = "https://dpc.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist"
SITE_GUID = "bc6e816e-4cfa-4317-9c8c-1e74c712d3fe"
CATEGORY_NUM = "003006"
TARGET_CATEGORY_NAME = "政策文件"
PAGE_SIZE = 20
MAX_PAGES = 100

HEADERS = {
    "Content-Type": "application/json",
    "Referer": TARGET_URL,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "ul", "ol", "table", "tr", "td", "th", "tbody", "thead",
               "blockquote", "pre", "hr"}

_ATTACHMENT_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".wps"}
_RELATED_KEYWORDS = ("解读", "图解", "相关阅读", "政策解读")
_INTERPRETATION_PREFIXES = (
    "政策解读：", "政策解读:", "解读文件：", "解读文件:",
    "相关解读：", "相关解读:",
)

_GENERIC_ALT = {"", "image", "img", "picture", "photo", "图片", "图像", "照片", "图标", "icon"}
_ICON_SRC_KEYWORDS = ("logo", "icon", "btn", "button", "arrow", "close", "search",
                       "qr", "code", "share", "print", "top", "home", "menu", "bg",
                       "banner", "ad-", "/ad/")


def _normalize_url(url, base_url):
    if not url:
        return url
    return urljoin(base_url, url)


def _strip_cjk_inner_ws(text):
    horizontal_ws = r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[\uff0c\uff01\uff1a\uff1b\uff1f\u3001\u3002\uff08\uff09\u300a\u300b\u300c\u300d\u3010\u3011\u3008\u3009\u300e\u300f\u201c\u201d\u2018\u2019]"

    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])", "", text)
    text = re.sub(rf"(?<=[A-Za-z0-9._%+-]){horizontal_ws}(?=@)", "", text)
    text = re.sub(rf"(?<=@){horizontal_ws}(?=[A-Za-z0-9.-])", "", text)
    return text


def _normalize_horizontal_ws(text):
    return re.sub(r"[ \t\u00a0\u3000]+", " ", text)


def _clean_title(title):
    text = _normalize_horizontal_ws(title)
    text = _strip_cjk_inner_ws(text)
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    left_bracket = r"[\u201c\u201d\u2018\u2019\uff08\uff09\u300a\u300b\u300c\u300d\u3010\u3011]"
    text = re.sub(rf"(?<={cjk})[ \t\u00a0\u3000]+(?={left_bracket})", "", text)
    text = re.sub(rf"(?<={left_bracket})[ \t\u00a0\u3000]+(?={cjk})", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_text_preserve_blocks(content_elem):
    paragraphs = []
    current_parts = []

    def _flush():
        nonlocal current_parts
        if current_parts:
            txt = "".join(current_parts).strip()
            if txt:
                paragraphs.append(txt)
            current_parts.clear()

    def _walk(node):
        if isinstance(node, NavigableString):
            if node.strip():
                current_parts.append(str(node))
            return
        if not isinstance(node, Tag) or not node.name:
            return
        tag_name = node.name.lower()
        if tag_name == "br":
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
    return "\n".join(paragraphs)


def _clean_content(content):
    if not content:
        return content
    paragraphs = content.split("\n")
    cleaned = []
    for para in paragraphs:
        para = _normalize_horizontal_ws(para)
        para = _strip_cjk_inner_ws(para)
        para = re.sub(r"[ \t]+", " ", para)
        para = para.strip()
        if para:
            cleaned.append(para)

    EMPTY_LABELS = {"附件下载：", "附件下载:", "视频：", "视频:"}
    while cleaned:
        last = cleaned[-1].strip()
        if last in EMPTY_LABELS:
            cleaned.pop()
        elif not last:
            cleaned.pop()
        else:
            break

    return "\n".join(cleaned)


def _is_meaningful_alt(alt):
    if not alt:
        return False
    alt_clean = alt.strip().lower()
    if alt_clean in _GENERIC_ALT or len(alt_clean) < 2:
        return False
    return True


def _is_icon_image(img_tag):
    src = (img_tag.get("src") or "").lower()
    if any(kw in src for kw in _ICON_SRC_KEYWORDS):
        return True
    for attr in ("width", "height"):
        val = img_tag.get(attr)
        if val:
            try:
                if int(str(val).replace("px", "")) < 50:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def _is_related_link(a_tag):
    text = a_tag.get_text(strip=True)
    href = a_tag.get("href") or ""
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
        if lowered.startswith("var fx"):
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
    return re.sub(r"[\s\u00a0\u3000]+", "", text).strip()


def _extract_attachments(soup, article_url):
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
    attachment_names = set()
    for p_tag in elem.find_all("p"):
        p_text = _clean_title(p_tag.get_text(" ", strip=True))
        if p_text and p_text in attachment_names:
            p_tag.decompose()
            continue
        a_tags = p_tag.find_all("a", href=True)
        if len(a_tags) == 1:
            a_tag = a_tags[0]
            a_href = (a_tag.get("href") or "").strip()
            a_text = _clean_title(a_tag.get_text(" ", strip=True))
            a_abs_url = _normalize_url(a_href, "")
            if a_abs_url in attachment_urls and a_text:
                attachment_names.add(a_text)
                p_tag.decompose()
    return elem


def _remove_duplicate_attachment_names(text, attachments):
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
    return "\n".join(kept_lines)


def _build_attachment_content(attachments):
    if not attachments:
        return ""
    parts = ["附件："]
    for name, url in attachments:
        parts.append(f"附件名称：{name}")
        parts.append(f"附件地址：{url}")
    return "\n".join(parts)


def _extract_images(content_elem, article_url):
    seen_urls = set()
    images = []
    for img in content_elem.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        if _is_icon_image(img):
            continue
        absolute_url = _normalize_url(src, article_url)
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        alt = img.get("alt") or img.get("title") or ""
        images.append((alt, absolute_url))
    return images


def _build_image_content(images):
    parts = []
    for alt, url in images:
        if _is_meaningful_alt(alt):
            parts.append(f"图片说明：{alt.strip()}")
        parts.append(f"图片地址：{url}")
    return "\n".join(parts)


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
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        if "您访问的页面不存在" in title_text:
            return True
        if "页面不存在" in title_text and "404" in title_text:
            return True
    body_text = soup.get_text()
    if len(body_text.strip()) < 100:
        return True
    return False


def _extract_content(session, article_url, metrics, title=""):
    try:
        response = session.get(article_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": TARGET_URL,
        }, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.content, "html.parser")

        if _is_error_page(soup):
            metrics.errors.append(f"详情页为错误页面: {article_url}")
            return ""

        attachments = _extract_attachments(soup, article_url)

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

        content = "\n".join(parts)

        if title:
            content = _remove_duplicate_title(content, title)

        content = _clean_content(content)

        return content
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def _fetch_list_page(session, page_index):
    payload = {
        "deptcode": "",
        "categorynum": CATEGORY_NUM,
        "pageIndex": page_index,
        "pageSize": PAGE_SIZE,
        "siteGuid": SITE_GUID,
    }
    resp = session.post(LIST_API_URL, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    try:
        return resp.json()
    except json.JSONDecodeError:
        return json.loads(resp.text)


def _parse_list_response(data):
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
    title = record.get("title") or record.get("realtitle") or ""
    detail_url = (
        record.get("infourl")
        or record.get("visiturl")
        or record.get("linkurl")
        or ""
    )
    pub_date_str = record.get("infodate") or record.get("handsdate") or ""
    categoryname = record.get("categoryname") or ""
    return {
        "title": title,
        "detail_url": detail_url,
        "pub_date_str": pub_date_str,
        "categoryname": categoryname,
    }


def scrape_data():
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

                title = _clean_title(info["title"])
                detail_url = info["detail_url"]
                pub_date_str = info["pub_date_str"]
                categoryname = info.get("categoryname") or ""

                if not title or not detail_url:
                    metrics.invalid_item_count += 1
                    continue

                if categoryname != TARGET_CATEGORY_NAME:
                    metrics.invalid_item_count += 1
                    continue

                if not detail_url.startswith("http"):
                    detail_url = urljoin(BASE_URL, detail_url.lstrip("/"))

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

            if oldest_date_on_page and oldest_date_on_page < target_from:
                break

            if not has_more or len(records) < PAGE_SIZE:
                break

            page_index += 1

    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))

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
