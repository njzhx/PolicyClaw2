import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://jsstyj.jiangsu.gov.cn/col/col79483/index.html"


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

        items = []
        
        target_div = soup.find('div', attrs={'aria-label': '正文区,综合政务'})
        if target_div:
            script_tag = target_div.find('script', type='text/xml')
            if script_tag and script_tag.string:
                datastore_soup = BeautifulSoup(script_tag.string, 'html.parser')
                records = datastore_soup.find_all('record')
                for record in records:
                    cdata = record.string
                    if cdata:
                        record_soup = BeautifulSoup(cdata, 'html.parser')
                        li_elems = record_soup.find_all('li')
                        items.extend(li_elems)
        
        if not items:
            all_links = soup.find_all('a', href=True)
            policy_links = [a for a in all_links if '/art/' in a.get('href', '')]
            for a_tag in policy_links:
                items.append(a_tag.parent if a_tag.parent else a_tag)

        filtered_count = 0

        for item in items:
            try:
                a_tag = item.find('a') if item.name != 'a' else item
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                href = a_tag.get('href', '').strip()

                if not title or not href or len(title) < 5:
                    continue

                if href.startswith('/'):
                    article_url = "https://jsstyj.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://jsstyj.jiangsu.gov.cn" + href
                else:
                    article_url = href

                pub_at = None
                date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', href)
                if date_match:
                    try:
                        pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                    except ValueError:
                        pass

                if not pub_at:
                    date_text = item.get_text()
                    date_match = re.search(r'(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})', date_text)
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
                    'source': '江苏省体育局政策文件'
                }
                policies.append(policy_data)

            except Exception as e:
                continue

        print(f'[OK] 江苏省体育局政策文件爬虫：成功抓取 {len(policies)} 条前一天数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省体育局政策文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省体育局_政策文件")
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
        print(f'[ERROR] 江苏省体育局政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()