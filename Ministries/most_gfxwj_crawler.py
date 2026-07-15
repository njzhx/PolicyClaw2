import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re
from urllib.parse import urljoin

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

TARGET_URL = "https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/gfxwj/"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"运行日期（北京时间）：{today}")
        print(f"目标抓取日期：{target_date_label}")

        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find data_list div
        data_list = soup.find(id='data_list')
        if not data_list:
            print("文章列表未找到")
            return policies, all_items

        lis = data_list.find_all('li')
        print(f"找到 {len(lis)} 条数据")

        filtered_count = 0

        for li in lis:
            try:
                a = li.find('a')
                if not a:
                    continue

                title = a.get_text(strip=True)
                href = a.get('href', '')

                if not title or not href:
                    continue

                # Convert relative URL to absolute
                if '/xxgk/xinxifenlei/' in href:
                    href = href.replace('/xxgk/xinxifenlei/', '/')
                full_url = urljoin("https://www.most.gov.cn/", href)

                # Extract date from URL path
                date_pattern = re.compile(r'/(\d{4})(\d{2})/t(\d{4})(\d{2})(\d{2})')
                match = date_pattern.search(href)
                pub_at = None
                if match:
                    date_str = f"{match.group(1)}-{match.group(2)}-{match.group(4)}"
                    try:
                        pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass

                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # Fetch detail page content
                content = ""
                try:
                    detail_resp = requests.get(full_url, headers=headers, timeout=15)
                    if detail_resp.status_code == 200:
                        detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                        # Try the specific XPath: //div[@class="xxgk_detail_content style1"]
                        content_div = detail_soup.find('div', class_='xxgk_detail_content style1')
                        if content_div:
                            content = content_div.get_text(separator='\n', strip=True)
                        else:
                            # Fallback: try other selectors
                            content_div = detail_soup.find('div', class_='TRS_Editor') or detail_soup.find('div', class_='content')
                            if content_div:
                                content = content_div.get_text(separator='\n', strip=True)
                            else:
                                max_text = ""
                                for tag in detail_soup.find_all(['div', 'td', 'p']):
                                    text = tag.get_text(strip=True)
                                    if len(text) > len(max_text):
                                        max_text = text
                                content = max_text
                except Exception as e:
                    print(f"抓取详情页失败：{e}")

                policy_data = {
                    'title': title,
                    'url': full_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '科技部规范性文件'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"单条数据处理失败 - {e}")
                continue

        print(f"\n✅ 科技部规范性文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n📊 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"✅ {title}... {date_str}")

    except Exception as e:
        print(f"❌ 科技部规范性文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "科技部规范性文件")
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
            print("✅ 爬虫 科技部规范性文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n💾 写入数据库: 0 条")
            print("----------------------------------------")
            print("⚠️  未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"❌ 爬虫 科技部规范性文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
