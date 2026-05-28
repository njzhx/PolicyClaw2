
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://wap.miit.gov.cn/zwgk/zcjd/index.html"
API_URL = "https://wap.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit"
API_PARAMS = {
    'parseType': 'buildstatic',
    'webId': '8d828e408d90447786ddbe128d495e9e',
    'tplSetId': '209741b2109044b5b7695700b2bec37e',
    'pageType': 'column',
    'tagId': '右侧内容',
    'editType': 'null',
    'pageId': '1b56e5adc362428299dfc3eb444fe23a'
}


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)



        response = requests.get(API_URL, headers=headers, params=API_PARAMS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        items = soup.find_all('li')
        filtered_count = 0

        for item in items:
            try:
                a_tag = item.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                title = title.strip('"\'\\')
                href = a_tag.get('href', '').strip('"\'\\')
                href = href.replace('\\', '').replace('"', '').replace("'", '')

                if not title or len(title) < 5:
                    continue

                if href.startswith('/'):
                    article_url = "https://wap.miit.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://wap.miit.gov.cn/zwgk/zcjd/" + href
                else:
                    article_url = href

                pub_at = None
                date_text = item.get_text()
                date_match = re.search(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})', date_text)
                if date_match:
                    try:
                        pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                    except ValueError:
                        pass

                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                    content_elem = detail_soup.find('div', class_='ccontent') or detail_soup.find('div', class_='content') or detail_soup.find('div', id='content')
                    if content_elem:
                        content = content_elem.get_text(strip=True)
                except Exception:
                    pass

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '工信部政策解读'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f"✅ 工信部政策解读爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")

        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")

    except Exception as e:
        print(f"❌ 工信部政策解读爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "工信部_政策解读")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 工信部政策解读爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()

