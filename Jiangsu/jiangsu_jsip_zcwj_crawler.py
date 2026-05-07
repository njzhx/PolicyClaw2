import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://jsip.jiangsu.gov.cn/col/col85038/index.html"


def scrape_data():
    policies = []
    all_items = []

    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)

        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        target_div = soup.find('div', class_='main-content-right zfxxgkzn-content fr')
        
        policy_links = {}
        
        if target_div:
            links = target_div.find_all('a', href=True)
            for a_tag in links:
                href = a_tag.get('href', '').strip()
                title = a_tag.get_text(strip=True)
                
                if '/art/' in href and title and len(title) > 3:
                    if href not in policy_links:
                        policy_links[href] = {
                            'title': title,
                            'href': href
                        }

        if not policy_links:
            all_links = soup.find_all('a', href=True)
            for a_tag in all_links:
                href = a_tag.get('href', '').strip()
                if '/art/' in href:
                    title = a_tag.get_text(strip=True)
                    if title and len(title) > 3 and href not in policy_links:
                        policy_links[href] = {
                            'title': title,
                            'href': href
                        }

        filtered_count = 0

        for item in policy_links.values():
            try:
                title = item['title']
                href = item['href']

                if href.startswith('/'):
                    article_url = "https://jsip.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://jsip.jiangsu.gov.cn" + href
                else:
                    article_url = href

                pub_at = None
                date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', href)
                if date_match:
                    try:
                        pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                    except ValueError:
                        pass

                all_items.append({'title': title, 'pub_at': pub_at})

                if pub_at != yesterday:
                    filtered_count += 1
                    continue

                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    for selector in ['#barrierfree_container', '.TRS_Editor', '#zoom', '.content', '#content', '.article-content', '.main-content', '.article']:
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
                    'source': '江苏省知识产权局政策文件'
                }
                policies.append(policy_data)

            except Exception as e:
                continue

        print(f'[OK] 江苏省知识产权局政策文件爬虫：成功抓取 {len(policies)} 条前一天数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省知识产权局政策文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省知识产权局_政策文件")
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
        print(f'[ERROR] 江苏省知识产权局政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()