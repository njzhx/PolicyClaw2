import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://mf.jiangsu.gov.cn/col/col49295/index.html"
API_URL = "https://mf.jiangsu.gov.cn/module/web/jpage/dataproxy.jsp?page=1&appid=1&webid=5&path=/&columnid=49295&unitid=434779&permissiontype=0"


def scrape_data():
    policies = []
    all_items = []

    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)

        response = requests.get(API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'xml')

        records = soup.find_all('record')
        policy_links = {}

        for record in records:
            cdata = record.string
            if cdata:
                record_soup = BeautifulSoup(cdata, 'html.parser')
                li_tag = record_soup.find('li')
                if li_tag:
                    a_tag = li_tag.find('a', href=True)
                    if a_tag:
                        title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                        href = a_tag.get('href', '').strip()
                        
                        span_tag = li_tag.find('span')
                        date_str = span_tag.get_text(strip=True) if span_tag else ''
                        
                        if title and href and len(title) > 5:
                            policy_links[href] = {
                                'title': title,
                                'href': href,
                                'date_str': date_str
                            }

        if not policy_links:
            response = requests.get(TARGET_URL, headers=headers, timeout=30)
            soup = BeautifulSoup(response.content, 'html.parser')
            all_links = soup.find_all('a', href=True)
            for a_tag in all_links:
                href = a_tag.get('href', '').strip()
                if '/art/' in href:
                    title = a_tag.get_text(strip=True)
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
                    article_url = "https://mf.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://mf.jiangsu.gov.cn" + href
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

                if pub_at != yesterday:
                    filtered_count += 1
                    continue

                content = ""
                attachments = []
                related_links = []
                
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
                    
                    all_links = detail_soup.find_all('a', href=True)
                    for link in all_links:
                        link_href = link.get('href', '')
                        link_text = link.get_text(strip=True)
                        
                        if '.pdf' in link_href.lower():
                            if link_href.startswith('/'):
                                pdf_url = "https://mf.jiangsu.gov.cn" + link_href
                            elif not link_href.startswith('http'):
                                pdf_url = "https://mf.jiangsu.gov.cn" + link_href
                            else:
                                pdf_url = link_href
                            attachments.append({'type': 'pdf', 'name': link_text, 'url': pdf_url})
                        
                        elif '/art/' in link_href and link_href != href:
                            if link_href.startswith('/'):
                                related_url = "https://mf.jiangsu.gov.cn" + link_href
                            elif not link_href.startswith('http'):
                                related_url = "https://mf.jiangsu.gov.cn" + link_href
                            else:
                                related_url = link_href
                            related_links.append({'name': link_text, 'url': related_url})

                except Exception as e:
                    print(f'[WARN] 抓取详情页失败: {article_url} - {e}')

                attachments_info = ""
                if attachments:
                    attachments_info = "\n\n[附件]\n"
                    for att in attachments:
                        attachments_info += f"- {att['name']}: {att['url']}\n"
                
                related_info = ""
                if related_links:
                    related_info = "\n\n[相关链接]\n"
                    for rel in related_links:
                        related_info += f"- {rel['name']}: {rel['url']}\n"

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content + attachments_info + related_info,
                    'selected': False,
                    'category': '',
                    'source': '江苏省国防动员办公室政策文件'
                }
                policies.append(policy_data)

            except Exception as e:
                continue

        print(f'[OK] 江苏省国防动员办公室政策文件爬虫：成功抓取 {len(policies)} 条前一天数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省国防动员办公室政策文件爬虫：抓取失败 - {e}')
        import traceback
        traceback.print_exc()
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省国防动员办公室_政策文件")
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
        print(f'[ERROR] 江苏省国防动员办公室政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()