import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re
from urllib.parse import urljoin

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://www.nea.gov.cn/policy/zxwj.htm"
LIST_JSON_URL = "https://www.nea.gov.cn/policy/ds_40d365c13659452aa06cdb7268d6192e.json"


def clean_title(value):
    text = BeautifulSoup(value or "", "html.parser").get_text("", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_publish_date(value):
    if not value:
        return None
    pub_str = str(value).strip()
    for fmt, size in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        if len(pub_str) >= size:
            try:
                return datetime.strptime(pub_str[:size], fmt).date()
            except ValueError:
                pass
    return None


def fetch_article_list():
    response = requests.get(
        LIST_JSON_URL,
        headers={**headers, "Referer": TARGET_URL},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("datasource") or []
    print(f"[INFO] 从 datasource JSON 获取列表: {len(items)} 条")
    return items


def extract_detail_url(item):
    for link in item.get("linkUrls") or []:
        link_url = link.get("linkUrl")
        if link_url:
            return urljoin(LIST_JSON_URL, link_url)

    detail_url = item.get("publishUrl") or item.get("url") or item.get("link") or ""
    return urljoin(LIST_JSON_URL, detail_url)


def fetch_detail_content(detail_url):
    detail_resp = requests.get(detail_url, headers=headers, timeout=15)
    detail_resp.raise_for_status()
    detail_resp.encoding = detail_resp.apparent_encoding
    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

    content_elem = detail_soup.find('td', class_='detail')
    if not content_elem:
        content_elem = detail_soup.find('span', id='detailContent')
    if not content_elem:
        content_elem = detail_soup.find('div', id='zoom')
    if not content_elem:
        content_elem = detail_soup.find('div', class_='TRS_Editor')
    if not content_elem:
        content_elem = detail_soup.find('div', id='content')

    if not content_elem:
        return ""

    text = content_elem.get_text(separator='\n', strip=True)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()
        print(f"[DATE] 运行日期（北京时间）：{today}")
        print(f"[TARGET] 目标抓取日期：{target_date_label}")

        items = fetch_article_list()

        filtered_count = 0

        for item in items:
            title = clean_title(item.get('title', ''))
            if not title:
                title = clean_title(item.get('showTitle', ''))

            publish_time = item.get('publishTime', '') or item.get('publishDate', '') or item.get('time', '')
            detail_url = extract_detail_url(item)

            if not title:
                continue

            pub_at = parse_publish_date(publish_time)

            all_items.append({'title': title, 'pub_at': pub_at})

            if not is_target_date(pub_at, target_date_from, target_date_to):
                filtered_count += 1
                continue

            content = ""
            try:
                content = fetch_detail_content(detail_url)
                if not content or len(content) < 50:
                    print(f'[WARN] 警告：文章内容可能未爬取成功 - {title[:50]}')
                    print(f'   链接: {detail_url}')
                    print(f'   内容长度: {len(content)} 字符')

            except Exception as e:
                print(f'[WARN] 抓取详情页失败: {detail_url} - {e}')

            policy_data = {
                'title': title,
                'url': detail_url,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '',
                'source': '国家能源局最新文件'
            }
            policies.append(policy_data)

        if not all_items:
            print('[INFO] 测试详情页抓取功能...')
            test_url = "http://www.nea.gov.cn/20260514/ded62aeb85294f51ab9597405dcd3449/c.html"
            try:
                test_resp = requests.get(test_url, headers=headers, timeout=15)
                test_resp.encoding = test_resp.apparent_encoding
                test_soup = BeautifulSoup(test_resp.content, 'html.parser')
                content_elem = test_soup.find('td', class_='detail')
                if content_elem:
                    content = content_elem.get_text(separator='\n', strip=True)
                    print(f'[OK] 详情页抓取测试成功，内容长度: {len(content)} 字符')
            except Exception as e:
                print(f'[ERROR] 详情页抓取测试失败: {e}')

        print(f'[OK] 国家能源局最新文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 国家能源局最新文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "国家能源局_最新文件")
    except Exception:
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f'[DB] 写入数据库: {len(result)} 条')
            print("----------------------------------------")
            print("[OK] 爬虫 国家能源局最新文件 执行成功")
            return result
        else:
            print("[DB] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章（或网站使用JavaScript动态加载需使用浏览器渲染）")
            return data
    except Exception as e:
        print(f'[ERROR] 爬虫 国家能源局最新文件 运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
