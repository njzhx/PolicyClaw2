import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Referer': 'https://ybj.jiangsu.gov.cn/col/col71878/index.html?number=02'
}

TARGET_URL = "https://ybj.jiangsu.gov.cn/col/col71878/index.html?number=02"
API_URL = "https://ybj.jiangsu.gov.cn/module/xxgk/search.jsp"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)

        post_data = {
            'divid': 'div71577',
            'infotypeId': '02',
            'jdid': '82',
            'area': 'MB1846817',
            'vc_title': '',
            'vc_number': '',
            'currpage': '1'
        }

        response = requests.post(API_URL, data=post_data, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        tables = soup.find_all('table')
        policy_links = {}

        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 3:
                    a_tag = row.find('a', href=True)
                    if a_tag and '/art/' in a_tag.get('href', ''):
                        title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                        href = a_tag.get('href', '').strip()
                        date_str = cells[-1].get_text(strip=True) if cells else ''

                        if title and href and len(title) > 5:
                            policy_links[href] = {
                                'title': title,
                                'href': href,
                                'date_str': date_str
                            }

        if not policy_links:
            all_links = soup.find_all('a', href=True)
            for a_tag in all_links:
                href = a_tag.get('href', '').strip()
                if '/art/' in href:
                    title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                    if title and len(title) > 5 and href not in policy_links:
                        policy_links[href] = {
                            'title': title,
                            'href': href,
                            'date_str': ''
                        }

        filtered_count = 0

        for item in policy_links.values():
            try:
                title = item['title']
                href = item['href']

                if href.startswith('/'):
                    article_url = "https://ybj.jiangsu.gov.cn" + href
                elif href.startswith('http://'):
                    article_url = href.replace('http://', 'https://')
                elif not href.startswith('http'):
                    article_url = "https://ybj.jiangsu.gov.cn" + href
                else:
                    article_url = href

                pub_at = None
                if item['date_str']:
                    try:
                        pub_at = datetime.strptime(item['date_str'], '%Y-%m-%d').date()
                    except ValueError:
                        pass

                if not pub_at:
                    date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', href)
                    if date_match:
                        try:
                            pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                        except ValueError:
                            pass

                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    for selector in ['#barrierfree_container', '.TRS_Editor', '#zoom', '.content', '#content', '.article-content', '.main-content']:
                        elem = detail_soup.select_one(selector)
                        if elem:
                            text = elem.get_text(separator='\n', strip=True)
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            if lines:
                                content = '\n'.join(lines)
                                break

                    if not content or len(content) < 50:
                        content_elem = detail_soup.find('div', class_='left')
                        if content_elem:
                            content = content_elem.get_text(separator='\n', strip=True)

                except Exception as e:
                    print(f'[WARN] 抓取详情页失败: {article_url} - {e}')

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '江苏省医疗保障局政策法规'
                }
                policies.append(policy_data)

            except Exception as e:
                continue

        print(f'[OK] 江苏省医疗保障局政策法规爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省医疗保障局政策法规爬虫：抓取失败 - {e}')
        import traceback
        traceback.print_exc()
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省医疗保障局_政策法规")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f'[DB] 写入数据库: {len(data)} 条')
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f'[ERROR] 江苏省医疗保障局政策法规爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()