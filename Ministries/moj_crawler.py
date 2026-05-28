import re
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.moj.gov.cn"
TARGET_URL = f"{BASE_URL}/pub/sfbgw/zwxxgk/zfxxgkzc/index.html?type=2"
LIST_API = f"{BASE_URL}/policyManager/policy/getPolicyDocList"
DETAIL_API = f"{BASE_URL}/policyManager/policy/getPolicyDocDetail"
SOURCE_NAME = "司法部行政规范性文件"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": BASE_URL,
    "Referer": TARGET_URL,
}


def parse_date(date_str):
    if not date_str:
        return None

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date_str))
    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).date()
    except ValueError:
        return None


def html_to_text(html):
    if not html:
        return ""
    soup = BeautifulSoup(unescape(str(html)), "html.parser")
    return soup.get_text(separator="\n", strip=True)


def build_article_url(article_id):
    return (
        f"{BASE_URL}/policyManager/policy_index.html"
        f"?showMenu=false&showFileType=2&pkid={article_id}"
    )


def fetch_policy_list(page_num=1, page_size=10):
    payload = {
        "pageNum": page_num,
        "pageSize": page_size,
        "file_type": "2",
        "validity": "1",
        "file_status": "1",
    }

    response = requests.post(LIST_API, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise RuntimeError(data.get("description") or data.get("errorMsg") or data)

    return data.get("list") or []


def fetch_policy_detail(article_id):
    if not article_id:
        return {}

    payload = {
        "pageNum": 1,
        "pageSize": 10,
        "file_type": "2",
        "validity": "1",
        "file_status": "1",
        "pkid": article_id,
    }

    response = requests.post(DETAIL_API, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise RuntimeError(data.get("description") or data.get("errorMsg") or data)

    return data.get("data") or {}


def get_article_content(url, article_id=None):
    try:
        detail = fetch_policy_detail(article_id)
        content = html_to_text(detail.get("document_content"))
        if content:
            return content
    except Exception as e:
        print(f"[WARN] 详情接口抓取失败，改用页面解析: {e}")

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        content_div = (
            soup.find("div", class_="newDeta w1200")
            or soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="content")
        )
        if content_div:
            return content_div.get_text(separator="\n", strip=True)
    except Exception as e:
        print(f"[WARN] 抓取详情页失败: {e}")

    return ""


def normalize_api_item(item):
    article_id = item.get("aritcleid") or item.get("articleid") or item.get("id")
    url = item.get("file_web_url") or item.get("url")
    if not url and article_id:
        url = build_article_url(article_id)
    elif url:
        url = urljoin(BASE_URL, url)

    title = item.get("document_title") or item.get("title") or ""
    pub_at = parse_date(item.get("release_date") or item.get("pulish_date"))

    return {
        "article_id": article_id,
        "title": title.strip(),
        "url": url,
        "pub_at": pub_at,
        "raw": item,
    }


def fetch_list_from_page():
    response = requests.get(TARGET_URL, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    div = soup.find(id="gfxwj_news_list")
    if not div:
        return []

    items = []
    for li in div.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue

        title = a.get_text(strip=True)
        href = urljoin(BASE_URL, a.get("href", ""))
        date_text = ""
        for span in li.find_all("span"):
            text = span.get_text(strip=True)
            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                date_text = text
                break

        if title and href:
            items.append(
                {
                    "article_id": None,
                    "title": title,
                    "url": href,
                    "pub_at": parse_date(date_text),
                    "raw": {},
                }
            )

    return items


def get_article_list(page_num=1, page_size=10):
    try:
        print("[INFO] 正在通过司法部政策接口获取文章列表...")
        api_items = fetch_policy_list(page_num=page_num, page_size=page_size)
        items = [normalize_api_item(item) for item in api_items]
        items = [item for item in items if item["title"] and item["url"]]
        print(f"[INFO] 接口返回 {len(items)} 条文章")
        return items
    except Exception as e:
        print(f"[WARN] 接口列表抓取失败，改用页面解析: {e}")
        items = fetch_list_from_page()
        print(f"[INFO] 页面解析得到 {len(items)} 条文章")
        return items


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）: {today}")
        print(f"[INFO] 目标抓取日期: {target_date_label}")

        all_items = get_article_list(page_num=1, page_size=10)
        filtered_count = 0

        for item in all_items:
            try:
                title = item["title"]
                article_url = item["url"]
                pub_at = item["pub_at"]

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = get_article_content(article_url, item.get("article_id"))
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

        print(f"\n[OK] 司法部行政规范性文件爬虫: 成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print("\n[INFO] 最新文章列表:")
            sorted_items = sorted(
                all_items,
                key=lambda x: x["pub_at"] or datetime.min.date(),
                reverse=True,
            )
            for item in sorted_items[:5]:
                date_str = item["pub_at"].strftime("%Y-%m-%d") if item["pub_at"] else "未知日期"
                print(f"[OK] {item['title'][:50]}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 司法部行政规范性文件爬虫抓取失败: {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy

        return save_to_policy(data_list, SOURCE_NAME)
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n[OK] 写入数据库 {len(result)} 条")
            print("----------------------------------------")
            print("[OK] 爬虫 司法部行政规范性文件 执行成功")
            return result, api_push_result

        print("\n[OK] 写入数据库 0 条")
        print("----------------------------------------")
        print("[WARN] 未找到目标日期的文章")
        return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 司法部行政规范性文件 运行失败: {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
