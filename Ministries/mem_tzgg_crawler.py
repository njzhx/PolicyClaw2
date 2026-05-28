import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://www.mem.gov.cn/gk/tzgg/"


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()
        print(f"[DATE] 运行日期（北京时间）：{today}")
        print(f"[TARGET] 目标抓取日期：{target_date_label}")

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.content, 'html.parser')

        container = soup.find('div', class_='tonglan_list')
        if not container:
            print('[ERROR] 应急管理部通知公告爬虫：未找到目标容器 div.tonglan_list')
            return policies, all_items

        cont_divs = container.find_all('div', class_='cont')
        if not cont_divs:
            print('[ERROR] 应急管理部通知公告爬虫：未找到内容容器 div.cont')
            return policies, all_items

        filtered_count = 0

        for cont_div in cont_divs:
            ul_element = cont_div.find('ul')
            if not ul_element:
                continue

            li_elements = ul_element.find_all('li')
            for li in li_elements:
                try:
                    a_tag = li.find('a')
                    if not a_tag:
                        continue

                    title = a_tag.get('title', '').strip()
                    if not title:
                        text_content = a_tag.contents
                        title_parts = []
                        for part in text_content:
                            if isinstance(part, str):
                                title_parts.append(part.strip())
                            elif hasattr(part, 'name') and part.name != 'span':
                                title_parts.append(part.get_text(strip=True))
                        title = ''.join(title_parts).strip()

                    title = re.sub(r'\d{4}-\d{2}-\d{2}$', '', title).strip()
                    title = re.sub(r'\s+', ' ', title).strip()

                    href = a_tag.get('href', '').strip()

                    if not title or not href:
                        continue

                    article_url = href
                    if not article_url.startswith('http'):
                        if article_url.startswith('/'):
                            article_url = "https://www.mem.gov.cn" + href
                        elif article_url.startswith('../'):
                            article_url = "https://www.mem.gov.cn/gk/" + href[3:]
                        else:
                            article_url = "https://www.mem.gov.cn/" + href

                    pub_at = None
                    span_tag = a_tag.find('span')
                    if span_tag:
                        span_text = span_tag.get_text(strip=True)
                        date_match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', span_text)
                        if date_match:
                            try:
                                pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                            except ValueError:
                                pass

                    if not pub_at:
                        date_match = re.search(r'/(\d{4})(\d{2})(\d{2})/', href)
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
                        detail_resp.encoding = detail_resp.apparent_encoding
                        detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                        content_elem = detail_soup.find('div', class_='TRS_Editor')
                        if not content_elem:
                            content_elem = detail_soup.find('div', class_='article')
                        if not content_elem:
                            content_elem = detail_soup.find('div', id='content')
                        if not content_elem:
                            content_elem = detail_soup.find('div', class_='main')

                        if content_elem:
                            text = content_elem.get_text(separator='\n', strip=True)
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            if lines:
                                content = '\n'.join(lines)

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
                        'source': '应急管理部通知公告'
                    }
                    policies.append(policy_data)

                except Exception:
                    continue

        print(f'[OK] 应急管理部通知公告爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 应急管理部通知公告爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "应急管理部_通知公告")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f'[DB] 写入数据库: {len(result)} 条')
            print("----------------------------------------")
            print("[OK] 爬虫 应急管理部通知公告 执行成功")
            return result
        else:
            print("[DB] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return data
    except Exception as e:
        print(f'[ERROR] 爬虫 应急管理部通知公告 运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()