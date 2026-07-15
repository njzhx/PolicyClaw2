import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

TARGET_URL = "https://www.mohrss.gov.cn/was5/web/search?channelid=203464&orderby=date&default=isall"
BASE_URL = "http://www.mohrss.gov.cn"


def parse_date(date_str):
    patterns = [
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})/(\d{2})/(\d{2})',
        r'(\d{4})年(\d{2})月(\d{2})日',
    ]
    for pattern in patterns:
        match = re.search(pattern, date_str)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
            except ValueError:
                continue
    return None


def get_article_content(url):
    content = ""
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            if 'javascript' in response.text.lower() and len(response.text) < 2000:
                print(f"[WARN] 详情页可能有反爬虫，跳过内容抓取")
                return content

            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            content_div = soup.find('div', class_='art_det')
            if content_div:
                content = content_div.get_text(separator='\n', strip=True)
            else:
                content_div = soup.find('div', id='zoom') or soup.find('div', class_='TRS_Editor')
                if content_div:
                    content = content_div.get_text(separator='\n', strip=True)

    except Exception as e:
        print(f"[WARN] 抓取详情页失败: {e}")
    return content


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）：{today}")
        print(f"[INFO] 目标抓取日期：{target_date_label}")

        print("[INFO] 获取搜索列表页面...")
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'

        if 'javascript' in response.text.lower() and len(response.text) < 2000:
            print("[ERROR] 列表页遇到反爬虫机制")
            return policies, all_items

        soup = BeautifulSoup(response.text, 'html.parser')

        iframe_list = soup.find('div', class_='iframe-list')
        if not iframe_list:
            print("[ERROR] 未找到 iframe-list 容器")
            return policies, all_items

        date_cells = iframe_list.find_all('td', class_='td-date')
        print(f"[INFO] 找到 {len(date_cells)} 条数据")

        filtered_count = 0

        for date_cell in date_cells:
            try:
                date_span = date_cell.find('span')
                date_str = date_span.get_text(strip=True) if date_span else ''

                next_td = date_cell.find_next_sibling('td')
                if not next_td:
                    continue

                a = next_td.find('a', href=True)
                if not a:
                    continue

                title = a.get_text(strip=True)
                href = a.get('href', '')

                if not title or not href:
                    continue

                if href.startswith('http'):
                    article_url = href
                elif href.startswith('/'):
                    article_url = BASE_URL + href
                else:
                    article_url = BASE_URL + '/' + href

                if article_url.endswith('?keywords='):
                    article_url = article_url[:-10]

                pub_at = parse_date(date_str)

                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = get_article_content(article_url)

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '人社部政策文件'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"[WARN] 单条数据处理失败 - {e}")
                continue

        print(f"\n[OK] 人社部政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n[INFO] 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"[OK] {title}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 人社部政策文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "人社部政策文件")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n[OK] 写入数据库: {len(result)} 条")
            print("----------------------------------------")
            print("[OK] 爬虫 人社部政策文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n[OK] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 人社部政策文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
