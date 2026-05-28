
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://scjgj.jiangsu.gov.cn/col/col78964/index.html"
DATAPROXY_URL = "https://scjgj.jiangsu.gov.cn/module/web/jpage/dataproxy.jsp?page=1&appid=1&appid=1&webid=79&path=/&columnid=78964&unitid=310641&permissiontype=0"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)

        # 访问 dataproxy 获取数据
        response = requests.get(DATAPROXY_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        records = soup.find_all('record')
        filtered_count = 0

        for record in records:
            try:
                cdata_content = record.string
                if not cdata_content:
                    continue

                li_soup = BeautifulSoup(cdata_content, 'html.parser')
                li_tag = li_soup.find('li')
                if not li_tag:
                    continue

                a_tag = li_tag.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip()
                if not title:
                    title = a_tag.get_text(strip=True)
                href = a_tag.get('href', '').strip()

                if not title or not href:
                    continue

                # 处理URL
                if href.startswith('/'):
                    article_url = "https://scjgj.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://scjgj.jiangsu.gov.cn" + href
                else:
                    article_url = href

                # 提取日期
                pub_at = None
                spans = li_tag.find_all('span')
                for span in spans:
                    span_text = span.get_text(strip=True)
                    date_match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', span_text)
                    if date_match:
                        try:
                            pub_at = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
                            break
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

                # 抓取详情页内容
                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    for selector in ['#barrierfree_container', 'div.main-content', '.TRS_Editor', '#zoom', '.content', '#content']:
                        elem = detail_soup.select_one(selector)
                        if elem:
                            text = elem.get_text(separator='\n', strip=True)
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            if lines:
                                content = '\n'.join(lines)
                                break

                    if not content or len(content) < 50:
                        print(f'[WARN] 警告：文章内容可能未爬取成功 - {title[:50]}')
                        print(f'   链接: {article_url}')
                        print(f'   内容长度: {len(content)} 字符')

                except Exception as e:
                    print(f'[WARN] 抓取详情页失败: {article_url} - {e}')

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '江苏省市场监管局政策文件'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] 江苏省市场监管局政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省市场监管局政策文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省市场监管局_政策文件")
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
        print(f'[ERROR] 江苏省市场监管局政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
