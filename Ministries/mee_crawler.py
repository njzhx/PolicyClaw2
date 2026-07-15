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

TARGET_URLS = {
    '部文_函': 'https://www.mee.gov.cn/zcwj/bwj/han/',
    '部文_公告': 'https://www.mee.gov.cn/zcwj/bwj/gg/',
    '部文_文件': 'https://www.mee.gov.cn/zcwj/bwj/wj/',
    '办公厅文_函': 'https://www.mee.gov.cn/zcwj/bgtwj/han/',
    '办公厅文_文件': 'https://www.mee.gov.cn/zcwj/bgtwj/wj/',
}

BASE_URL = 'https://www.mee.gov.cn'


def parse_date(date_str):
    patterns = [
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})/(\d{2})/(\d{2})',
        r'(\d{4})年(\d{2})月(\d{2})日',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
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
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        content_box = soup.find(class_='content_box')
        if content_box:
            divs = content_box.find_all('div')
            for div in divs:
                text = div.get_text(strip=True)
                if text:
                    content += text + '\n\n'
            content = content.strip()
    except Exception as e:
        print(f"[WARN] 抓取详情页失败: {e}")
    return content


def scrape_data(source_name, url):
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）：{today}")
        print(f"[INFO] 目标抓取日期：{target_date_label}")

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        div_container = soup.find(id='div')
        if not div_container:
            print(f"[ERROR] 未找到文章列表容器 id='div'")
            return policies, all_items

        lis = div_container.find_all('li')
        print(f"[INFO] 找到 {len(lis)} 条数据")

        filtered_count = 0

        for li in lis:
            try:
                a = li.find('a', href=True)
                if not a:
                    continue

                title = a.get_text(strip=True)
                href = a.get('href', '')

                if not title or not href:
                    continue

                article_url = href
                if href.startswith('../'):
                    article_url = BASE_URL + href[2:]
                elif href.startswith('./'):
                    article_url = BASE_URL + href[1:]
                elif not href.startswith('http'):
                    article_url = BASE_URL + href

                date_span = li.find('span', class_='date')
                date_str = date_span.get_text(strip=True) if date_span else ''

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
                    'source': f'生态环境部_{source_name}'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"[WARN] 单条数据处理失败 - {e}")
                continue

        print(f"\n[OK] 生态环境部_{source_name}：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n[INFO] 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"[OK] {title}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 生态环境部_{source_name}：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "生态环境部")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    all_results = []
    api_push_results = []

    for source_name, url in TARGET_URLS.items():
        print(f"\n=========================================")
        print(f"[INFO] 开始抓取 生态环境部_{source_name}")
        print(f"[INFO] 目标网址: {url}")
        print("----------------------------------------")

        try:
            data, _ = scrape_data(source_name, url)
            if data:
                result, api_push_result = save_to_supabase(data)
                all_results.extend(result)
                api_push_results.append(api_push_result)
                print(f"\n[OK] 写入数据库: {len(result)} 条")
                print("----------------------------------------")
            else:
                print(f"\n[OK] 写入数据库: 0 条")
                print("----------------------------------------")
        except Exception as e:
            print(f"[ERROR] 爬虫 生态环境部_{source_name} 运行失败 - {e}")
            print("----------------------------------------")

    print("\n=========================================")
    print(f"[OK] 爬虫 生态环境部 执行完成")
    print(f"[INFO] 总抓取数据: {len(all_results)} 条")
    print("----------------------------------------")

    return all_results, api_push_results


if __name__ == "__main__":
    run()
