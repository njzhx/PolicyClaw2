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

TARGET_URL = "https://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）：{today}")
        print(f"[INFO] 目标抓取日期：{target_date_label}")

        print("正在获取页面列表...")
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'

        soup = BeautifulSoup(response.text, 'html.parser')

        mainbox = soup.find('div', class_='mainboxerji')
        if not mainbox:
            print("[ERROR] 未找到文章列表容器 (div.mainboxerji)")
            return policies, all_items

        xwfb_list = mainbox.find('div', class_='xwfb_listerji')
        if not xwfb_list:
            print("[ERROR] 未找到列表容器 (div.xwfb_listerji)")
            return policies, all_items

        uls = xwfb_list.find_all('ul', class_='xwfb_listbox')
        print(f"[INFO] 找到 {len(uls)} 个列表容器")

        filtered_count = 0

        for ul in uls:
            lis = ul.find_all('li')
            for li in lis:
                try:
                    a = li.find('a')
                    span = li.find('span')

                    if not a:
                        continue

                    title = a.get_text(strip=True)
                    href = a.get('href', '')

                    if not title or not href:
                        continue

                    if not href.startswith('http'):
                        if href.startswith('/'):
                            href = f"https://www.mof.gov.cn{href}"
                        else:
                            href = f"https://www.mof.gov.cn/{href}"

                    date_str = span.get_text(strip=True) if span else ''
                    pub_at = None
                    if date_str and re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                        try:
                            pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    all_items.append({'title': title, 'pub_at': pub_at})

                    if not is_target_date(pub_at, target_date_from, target_date_to):
                        filtered_count += 1
                        continue

                    content = ""
                    try:
                        detail_resp = requests.get(href, headers=headers, timeout=15)
                        if detail_resp.status_code == 200:
                            detail_resp.encoding = 'utf-8'
                            detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')

                            content_div = detail_soup.find('div', class_='my_conboxzw')
                            if content_div:
                                content = content_div.get_text(separator='\n', strip=True)
                            else:
                                content_div = detail_soup.find('div', class_='TRS_Editor') or detail_soup.find('div', class_='content')
                                if content_div:
                                    content = content_div.get_text(separator='\n', strip=True)
                    except Exception as e:
                        print(f"[WARN] 抓取详情页失败：{e}")

                    policy_data = {
                        'title': title,
                        'url': href,
                        'pub_at': pub_at,
                        'content': content,
                        'selected': False,
                        'category': '',
                        'source': '财政部政策文件'
                    }

                    policies.append(policy_data)

                except Exception as e:
                    print(f"[WARN] 单条数据处理失败 - {e}")
                    continue

        print(f"\n[OK] 财政部政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n[INFO] 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"[OK] {title}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 财政部政策文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "财政部政策文件")
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
            print("[OK] 爬虫 财政部政策文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n[OK] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 财政部政策文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
