import re
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
from urllib.parse import quote, unquote, urljoin

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning


requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


BASE_URL = "https://www.nhc.gov.cn"
TARGET_URL = f"{BASE_URL}/wjw/gfxwjj/list.shtml"
READER_PREFIX = "https://r.jina.ai/http://r.jina.ai/http://"
SOURCE_NAME = "国家卫生健康委员会规范性文件"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def parse_date(text):
    if not text:
        return None

    match = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", str(text))
    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).date()
    except ValueError:
        return None


def clean_lines(text):
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def reader_url(url):
    return READER_PREFIX + url


def fetch_text(url, session=None, use_reader=False, timeout=30):
    client = session or requests.Session()
    target = reader_url(url) if use_reader else url
    response = client.get(target, headers=headers, timeout=timeout, verify=False)
    print(f"[INFO] 响应状态码: {response.status_code} - {target}")

    if response.status_code == 412 and not use_reader:
        raise RuntimeError("官网触发 412 WAF 拦截")
    if response.status_code == 451 and use_reader:
        raise RuntimeError(f"Reader 暂时限制访问: {response.text[:200]}")

    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def parse_html_list(html):
    soup = BeautifulSoup(html, "html.parser")
    ul_element = soup.find("ul", class_="zxxx_list") or soup.find("ul")
    if not ul_element:
        return []

    items = []
    for li in ul_element.find_all("li"):
        a_tag = li.find("a", href=True)
        if not a_tag:
            continue

        title = (a_tag.get("title") or a_tag.get_text(strip=True)).strip()
        href = a_tag.get("href", "").strip()
        if not title or not href:
            continue

        article_url = urljoin(TARGET_URL, href)
        span_text = li.get_text(" ", strip=True)
        pub_at = parse_date(span_text) or parse_date(href)
        items.append({"title": title, "url": article_url, "pub_at": pub_at})

    return items


def parse_reader_list(markdown):
    items = []
    pattern = re.compile(
        r"^\s*[-*]\s*\[(?P<title>.+?)\]\((?P<url>https?://www\.nhc\.gov\.cn/.+?)\)\s*(?P<date>\d{4}[-/]\d{2}[-/]\d{2})?\s*$"
    )
    plain_pattern = re.compile(r"^\s*[-*]\s*(?P<title>.+?)(?P<date>\d{4}-\d{2}-\d{2})\s*$")

    for line in markdown.splitlines():
        line = line.strip()
        match = pattern.search(line)
        if match:
            title = match.group("title").strip()
            url = match.group("url").strip()
            pub_at = parse_date(match.group("date"))
            items.append({"title": title, "url": url, "pub_at": pub_at})
            continue

        match = plain_pattern.search(line)
        if match:
            title = match.group("title").strip()
            pub_at = parse_date(match.group("date"))
            if title and "规范性文件" not in title:
                items.append({"title": title, "url": "", "pub_at": pub_at})

    # Reader occasionally emits duplicate navigation/footer links; keep real article rows only.
    seen = set()
    unique_items = []
    for item in items:
        key = (item["title"], item["pub_at"])
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    return unique_items


def parse_google_indexed_list(markdown):
    items = []

    # Google's result page exposes indexed text fragments in the "Read more" URL.
    # They look like: text=<title>2026-04-30.
    for encoded in re.findall(r"[?&#]text=([^&\)\s]+)", markdown):
        text = unquote(encoded)
        text = re.sub(r"\s+", " ", text).strip()
        match = re.search(r"(?P<title>.+?)(?P<date>\d{4}-\d{2}-\d{2})$", text)
        if not match:
            continue

        title = match.group("title").strip(" ·-—")
        pub_at = parse_date(match.group("date"))
        # 严格验证标题，过滤掉无效标题
        if title and pub_at and len(title) > 3 and "time:" not in title.lower():
            items.append({"title": title, "url": TARGET_URL, "pub_at": pub_at})

    # The visible snippet is less structured, but useful when Google omits fragments.
    snippet_matches = re.findall(r"([^·\n]+?)(\d{4}-\d{2}-\d{2})", markdown)
    for title, date_text in snippet_matches:
        title = re.sub(r"^[\s_*·>-]+", "", title).strip()
        # 严格过滤无效标题
        if not title or "site:" in title or "Google" in title or len(title) < 3:
            continue
        # 过滤常见的导航标题
        if title.lower() in ["time:", "date:", "read more", "more results", "about this result"]:
            continue
        pub_at = parse_date(date_text)
        if pub_at:
            items.append({"title": title, "url": TARGET_URL, "pub_at": pub_at})

    seen = set()
    unique_items = []
    for item in items:
        key = (item["title"], item["pub_at"])
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    return unique_items


def fetch_google_indexed_list(session):
    query = quote("site:nhc.gov.cn/wjw/gfxwjj/list.shtml")
    google_url = f"https://www.google.com/search?q={query}"
    markdown = fetch_text(google_url, session=session, use_reader=True, timeout=90)
    items = parse_google_indexed_list(markdown)
    print(f"[INFO] Google 索引兜底解析得到 {len(items)} 条文章")
    return items


def resolve_indexed_article_url(title, session):
    if not title:
        return ""

    query = quote(f'site:nhc.gov.cn "{title}"')
    google_url = f"https://www.google.com/search?q={query}"
    try:
        markdown = fetch_text(google_url, session=session, use_reader=True, timeout=90)
    except Exception as e:
        print(f"[WARN] 标题反查 URL 失败: {title[:40]} - {e}")
        return ""

    for url in re.findall(r"https://www\.nhc\.gov\.cn/[^\)\]\s]+", markdown):
        if "/wjw/gfxwjj/list" in url:
            continue
        if "google.com" in url:
            continue
        return url.rstrip(".,")

    return ""


def get_article_list(session):
    try:
        html = fetch_text(TARGET_URL, session=session)
        items = parse_html_list(html)
        if items:
            print(f"[INFO] 官网页面解析得到 {len(items)} 条文章")
            return items, "nhc"
        print("[WARN] 官网页面未解析到列表，尝试 Reader 兜底")
    except Exception as e:
        print(f"[WARN] 官网列表抓取失败，尝试 Reader 兜底: {e}")

    try:
        markdown = fetch_text(TARGET_URL, session=session, use_reader=True, timeout=90)
        if "Target URL returned error 412" not in markdown:
            items = parse_reader_list(markdown)
            print(f"[INFO] Reader 解析得到 {len(items)} 条文章")
            if items:
                return items, "reader"
        print("[WARN] Reader 只拿到 412 页面，尝试 Google 索引兜底")
    except Exception as e:
        print(f"[WARN] Reader 列表抓取失败，尝试 Google 索引兜底: {e}")

    items = fetch_google_indexed_list(session)
    return items, "indexed"


def parse_html_content(html):
    soup = BeautifulSoup(html, "html.parser")
    content_elem = (
        soup.find("div", class_="content")
        or soup.find("div", class_="article")
        or soup.find("div", id="content")
        or soup.find("div", class_=lambda x: x and any(k in x for k in ("main", "body", "text", "con")))
    )

    if not content_elem:
        return ""
    return clean_lines(content_elem.get_text(separator="\n", strip=True))


def parse_reader_content(markdown):
    marker = "Markdown Content:"
    if marker in markdown:
        markdown = markdown.split(marker, 1)[1]
    return clean_lines(re.sub(r"!\[.*?\]\(.*?\)", "", markdown))


def get_article_content(url, session, source):
    if not url:
        return ""

    try:
        if source == "reader":
            return parse_reader_content(fetch_text(url, session=session, use_reader=True, timeout=60))
        return parse_html_content(fetch_text(url, session=session, timeout=30))
    except Exception as e:
        print(f"[WARN] 详情抓取失败，尝试 Reader 兜底: {url} - {e}")
        try:
            return parse_reader_content(fetch_text(url, session=session, use_reader=True, timeout=60))
        except Exception as reader_error:
            print(f"[WARN] Reader 详情兜底失败: {url} - {reader_error}")
            return ""


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()
        print(f"[DATE] 运行日期（北京时间）: {today}")
        print(f"[TARGET] 目标抓取日期: {target_date_label}")

        session = requests.Session()
        session.headers.update(headers)

        all_items, source = get_article_list(session)
        if not all_items:
            print("[ERROR] 国家卫生健康委员会规范性文件爬虫: 列表为空")
            return policies, all_items

        filtered_count = 0
        for item in all_items:
            try:
                title = item["title"]
                article_url = item["url"]
                pub_at = item["pub_at"]

                # 严格验证数据，过滤无效数据
                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # 验证标题有效性
                if not title or len(title) < 3 or title.lower() in ["time:", "date:"]:
                    print(f"[WARN] 跳过无效标题: {title}")
                    filtered_count += 1
                    continue

                if source == "indexed":
                    resolved_url = resolve_indexed_article_url(title, session)
                    if resolved_url:
                        article_url = resolved_url
                    else:
                        # 如果没有找到有效的文章URL，跳过这条记录
                        print(f"[WARN] 无法解析文章URL，跳过: {title[:40]}")
                        filtered_count += 1
                        continue

                content = get_article_content(article_url, session, source)
                if not content or len(content) < 50:
                    print(f"[WARN] 文章内容可能未完整抓取: {title[:50]} ({len(content)} 字)")
                    # 内容太短时也跳过，避免保存无效数据
                    filtered_count += 1
                    continue

                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": content,
                        "selected": False,
                        "category": "",
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as e:
                print(f"[WARN] 单条数据处理失败: {e}")
                continue

        print(f"[OK] 国家卫生健康委员会规范性文件爬虫: 成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        print("[INFO] 页面最新文章:")
        for i, item in enumerate(all_items[:5], 1):
            date_str = item["pub_at"].strftime("%Y-%m-%d") if item["pub_at"] else "未知日期"
            print(f"  {i}. {item['title'][:60]}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 国家卫生健康委员会规范性文件爬虫抓取失败: {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy

        return save_to_policy(data_list, "国家卫生健康委员会_规范性文件")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"[DB] 写入数据库: {len(result)} 条")
            print("----------------------------------------")
            print("[OK] 爬虫 国家卫生健康委员会规范性文件 执行成功")
            return result, api_push_result

        print("[DB] 写入数据库: 0 条")
        print("----------------------------------------")
        print("[WARN] 未找到目标日期的文章")
        return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 国家卫生健康委员会规范性文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()