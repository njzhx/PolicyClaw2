"""
宿迁市信访局_政策文件爬虫
目标页面：https://xfj.suqian.gov.cn/sqxfj/zcwj/xxgk_list.shtml
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


TARGET_URL = 'https://xfj.suqian.gov.cn/sqxfj/zcwj/xxgk_list.shtml'
SOURCE_NAME = "宿迁市信访局_政策文件"
CATEGORY = "宿迁"
BASE_URL = 'https://xfj.suqian.gov.cn/'

_ATTACHMENT_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".wps"}
_RELATED_KEYWORDS = ("解读", "图解", "相关阅读", "政策解读", "相关推荐")
_INTERPRETATION_PREFIXES = (
    "政策解读：", "政策解读:", "解读文件：", "解读文件:",
    "相关解读：", "相关解读:",
)
_GENERIC_ALT = {"", "image", "img", "picture", "photo", "图片", "图像", "照片", "图标", "icon"}
_ICON_SRC_KEYWORDS = (
    "logo", "icon", "btn", "button", "arrow", "close", "search",
    "qr", "code", "share", "print", "top", "home", "menu", "bg",
    "banner", "ad-", "/ad/"
)
_EXCLUDE_LINKS = (
    "申请公开", "申请政府信息", "ysqgk", "ysq_gk"
)


def _strip_cjk_inner_ws(text):
    """删除HTML排版造成的中文横向空白"""
    if not text:
        return text
    horizontal_ws = r"[ \t\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000]+"
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff]"
    cjk_punct = r"[\uff0c\uff01\uff1a\uff1b\uff1f\u3001\u3002\uff08\uff09\u300a\u300b\u300c\u300d\u3010\u3011\u3008\u3009\u300e\u300f\u201c\u201d\u2018\u2019]"
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk})", "", text)
    text = re.sub(rf"(?<={cjk_punct}){horizontal_ws}(?={cjk_punct})", "", text)
    text = re.sub(rf"(?<=\d){horizontal_ws}(?=[年月日条项章款号])", "", text)
    return text


def _normalize_ws(text):
    """统一横向空白为普通空格"""
    return re.sub(r"[ \t\u00a0\u3000]+", " ", text)


def _clean_title(title):
    """清理标题"""
    text = _normalize_ws(title)
    text = _strip_cjk_inner_ws(text)
    return text.strip()


def _is_policy_link(href, link_text=""):
    """判断是否为政策文件详情链接"""
    if not href:
        return False
    href_lower = href.lower()
    # 排除申请公开等外链
    for kw in _EXCLUDE_LINKS:
        if kw in href_lower:
            return False
    # 只接受 xfj.suqian.gov.cn/sqxfj/zcwj/ 开头的政策详情链接
    return "sqxfj/zcwj" in href_lower or "/zcwj/" in href_lower


def _extract_pub_at_from_li(li_tag):
    """从li节点文本提取发布日期"""
    text = li_tag.get_text(" ", strip=True)
    # 匹配 YYYY-MM-DD 格式
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    return None


def _extract_list_page(session, url):
    """抓取列表页"""
    resp = session.get(url, timeout=15)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _parse_list(raw_html):
    """解析列表页，提取政策条目"""
    soup = BeautifulSoup(raw_html, "html.parser")
    items = []
    for li in soup.select("ul.listContent > li"):
        a_tag = li.select_one("a[href]")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if not _is_policy_link(href):
            continue
        title = a_tag.get_text(strip=True)
        if not title:
            continue
        pub_at_str = _extract_pub_at_from_li(li)
        items.append({
            "title": title,
            "url": urljoin(BASE_URL, href),
            "pub_at_str": pub_at_str,
        })
    return items


def _fetch_detail_title(session, url):
    """获取详情页<title>并清理末尾机构名"""
    try:
        resp = session.get(url, timeout=15)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.content, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            full_title = title_tag.get_text(strip=True)
            # 去掉末尾机构名
            for suffix in ("-宿迁市信访局", "_宿迁市信访局", "-宿迁信访局"):
                if full_title.endswith(suffix):
                    return full_title[:-len(suffix)].strip()
            return full_title.strip()
    except Exception:
        pass
    return None


def _is_attachment_link(a_tag):
    """判断是否为下载附件链接"""
    href = (a_tag.get("href") or "")
    if not href:
        return False
    href_lower = href.lower()
    if any(href_lower.startswith(p) for p in ("#", "javascript:", "mailto:", "tel:")):
        return False
    # 必须有文件扩展名
    path = href.lower()
    return any(path.endswith(ext) for ext in _ATTACHMENT_EXTS)


def _extract_attachments(soup):
    """从详情页提取附件"""
    seen = set()
    attachments = []
    for a in soup.find_all("a", href=True):
        if _is_attachment_link(a):
            url = urljoin(BASE_URL, a.get("href"))
            if url in seen:
                continue
            seen.add(url)
            name = a.get_text(strip=True)
            if not name:
                from urllib.parse import urlsplit, unquote
                name = unquote(urlsplit(url).path.rsplit("/", 1)[-1] or "")
            name = _strip_cjk_inner_ws(name)
            attachments.append((name, url))
    return attachments


def _remove_noise_elements(content_elem):
    """删除脚本、样式及功能链接"""
    for tag in ("script", "style", "noscript", "iframe"):
        for node in content_elem.select(tag):
            node.decompose()
    noise = {"关闭本页", "打印本页", "返回顶部", "网站地图"}
    for a in content_elem.select("a"):
        text = _clean_title(a.get_text(" ", strip=True))
        if text in noise:
            a.decompose()
    return content_elem


def _extract_text_preserve(content_elem):
    """遍历DOM提取正文段落"""
    paragraphs = []
    buf = []

    def flush():
        if buf:
            line = "".join(buf).strip()
            if line:
                paragraphs.append(line)
            buf.clear()

    def walk(node):
        if isinstance(node, NavigableString):
            if node.strip():
                buf.append(str(node.strip()))
            return
        if not isinstance(node, Tag):
            return
        nm = node.name.lower()
        if nm == "br":
            flush()
            return
        if nm in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "li", "ul", "ol", "table", "tr", "td", "blockquote", "pre", "hr"}:
            flush()
            for child in node.children:
                walk(child)
            flush()
        else:
            for child in node.children:
                walk(child)

    walk(content_elem)
    flush()
    return "\n".join(paragraphs)


def _final_clean(text):
    """对正文段落统一横向空白并清理中文排版空格"""
    lines = []
    for line in text.splitlines():
        line = _normalize_ws(line)
        line = _strip_cjk_inner_ws(line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def scrape_data():
    """抓取列表并处理详情页"""
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    raw_html = _extract_list_page(session, TARGET_URL)
    items = _parse_list(raw_html)
    metrics.raw_item_count = len(items)

    for item in items:
        title = _clean_title(item["title"])
        detail_url = item["url"]
        pub_at_str = item["pub_at_str"]
        pub_at = parse_date(pub_at_str) if pub_at_str else None
        if not title or not detail_url:
            metrics.invalid_item_count += 1
            continue
        if not pub_at:
            metrics.invalid_item_count += 1
            metrics.errors.append(f"无法解析日期: {title[:30]}")
            continue
        metrics.valid_item_count += 1
        # 获取完整标题
        full_title = _fetch_detail_title(session, detail_url) or title
        full_title = _clean_title(full_title)

        latest_items.append({"title": full_title, "pub_at": pub_at})
        if not is_target_date(pub_at, target_from, target_to):
            metrics.filtered_count += 1
            continue
        # 抓详情页正文
        content_text = ""
        try:
            resp = session.get(detail_url, timeout=15)
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.content, "html.parser")
            # 清理正文容器外层脚本和样式
            _remove_noise_elements(soup)
            article = soup.select_one(".article-content")
            if article:
                _remove_noise_elements(article)
                for meta in article.select("table.info, table.t1, .article-meta, .info-list"):
                    meta.decompose()
                content_text = _extract_text_preserve(article)
                content_text = _final_clean(content_text)
        except Exception as exc:
            metrics.errors.append(f"详情页异常: {detail_url}: {exc}")
        policies.append({
            "title": full_title,
            "url": detail_url,
            "pub_at": pub_at,
            "content": content_text,
            "selected": False,
            "category": CATEGORY,
            "source": SOURCE_NAME,
        })

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for p in policies if not p.get("content"))
    return policies, latest_items[:5], metrics


def run():
    data, latest_items, metrics = scrape_data()
    processed, api_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(
        items=processed,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_result,
    )


if __name__ == "__main__":
    run()
