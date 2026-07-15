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

TARGET_URL = "https://gi.mnr.gov.cn/1285/1317/index_6200.html"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）：{today}")
        print(f"[INFO] 目标抓取日期：{target_date_label}")

        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        listfr = soup.find('div', class_='listfr')
        if not listfr:
            print("[ERROR] 未找到文章列表容器")
            return policies, all_items

        table = listfr.find('table', class_='table')
        if not table:
            print("[ERROR] 未找到文章列表表格")
            return policies, all_items

        rows = table.find_all('tr')
        print(f"[INFO] 找到 {len(rows)} 行数据")

        filtered_count = 0
        article_count = 0

        for row in rows:
            try:
                # 跳过没有足够td的行
                tds = row.find_all('td')
                if len(tds) < 22:
                    continue

                # 获取标题
                title_td = tds[1]
                a = title_td.find('a')
                if not a:
                    continue

                title = a.get_text(strip=True)
                href = a.get('href', '')

                if not title:
                    continue

                # 获取发布日期 - 在td 21中
                date_td = tds[21]
                date_text = date_td.get_text(strip=True)

                # 转换日期格式: 2026年01月07日 -> 2026-01-07
                date_match = re.search(r'(\d{4})年(\d{2})月(\d{2})日', date_text)
                pub_at = None
                date_str = ''
                if date_match:
                    date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                    try:
                        pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass

                all_items.append({'title': title, 'pub_at': pub_at})
                article_count += 1

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # 构建文章链接 - 虽然href是空的，但保留结构
                article_url = ''

                content = ""
                # 由于链接为空，暂时跳过内容抓取
                # 可以通过其他方式获取，但目前暂留空

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '自然资源部政策文件'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"[WARN] 单条数据处理失败 - {e}")
                continue

        print(f"\n[OK] 自然资源部政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n[INFO] 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"[OK] {title}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 自然资源部政策文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "自然资源部政策文件")
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
            print("[OK] 爬虫 自然资源部政策文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n[OK] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 自然资源部政策文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
