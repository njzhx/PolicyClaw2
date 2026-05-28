import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.mca.gov.cn/gdnps/pc/index.jsp?mtype=1',
}

TARGET_URL = "https://www.mca.gov.cn/gdnps/searchIndex.jsp?params=%257B%2522goPage%2522%253A1%252C%2522orderBy%2522%253A%255B%257B%2522orderBy%2522%253A%2522scrq%2522%252C%2522reverse%2522%253Atrue%257D%252C%257B%2522orderBy%2522%253A%2522orderTime%2522%252C%2522reverse%2522%253Atrue%257D%255D%252C%2522pageSize%2522%253A15%252C%2522queryParam%2522%253A%255B%257B%2522shortName%2522%253A%2522ownSubjectDn%2522%252C%2522value%2522%253A%2522%252F1%252F139%252F2445%252F2575%2522%257D%252C%257B%2522shortName%2522%253A%2522fbjg%2522%252C%2522value%2522%253A%2522%252F1%252F139%252F2445%252F2575%2522%257D%252C%257B%257D%252C%257B%257D%255D%252C%2522doRepeated%2522%253A0%257D"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"📅 运行日期（北京时间）：{today}")
        print(f"🎯 目标抓取日期：{target_date_label}")

        print("正在从API获取数据...")
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()

        # Parse JSON response
        text = response.text.strip()
        if text.startswith('('):
            text = text[1:]
        if text.endswith(')'):
            text = text[:-1]

        json_start = text.find('{')
        json_end = text.rfind('}')
        if json_start != -1 and json_end != -1 and json_end > json_start:
            text = text[json_start:json_end + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            pos = e.pos
            start = max(0, pos - 50)
            end = min(len(text), pos + 50)
            context = text[start:end]
            print(f"❌ JSON解析失败 - 错误位置附近: ...{context}...")
            raise
        all_records = data.get('resultMap', [])
        print(f"📋 API返回 {len(all_records)} 条数据")

        filtered_count = 0

        for record in all_records:
            try:
                title = record.get('title', '')
                publish_time_str = record.get('publishTime', '')
                html_content = record.get('htmlContent', '')

                if not title:
                    continue

                # Parse publishTime: "20260429095000" -> date
                pub_at = None
                if publish_time_str:
                    try:
                        # Format: YYYYMMDDHHmmss
                        pub_at = datetime.strptime(publish_time_str[:8], '%Y%m%d').date()
                    except ValueError:
                        pass

                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # Extract text from HTML content
                content = ""
                if html_content:
                    content_soup = BeautifulSoup(html_content, 'html.parser')
                    content = content_soup.get_text(separator='\n', strip=True)

                # Build URL from record
                url = f"https://www.mca.gov.cn{record.get('url', '')}"

                policy_data = {
                    'title': title,
                    'url': url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '民政部政策文件'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"⚠️  单条数据处理失败 - {e}")
                continue

        print(f"\n✅ 民政部政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n📊 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"✅ {title}... {date_str}")

    except Exception as e:
        print(f"❌ 民政部政策文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "民政部政策文件")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n💾 写入数据库: {len(result)} 条")
            print("----------------------------------------")
            print("✅ 爬虫 民政部政策文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n💾 写入数据库: 0 条")
            print("----------------------------------------")
            print("⚠️  未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"❌ 爬虫 民政部政策文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
