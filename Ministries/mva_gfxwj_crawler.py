import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TARGET_URL = "https://www.mva.gov.cn/gongkai/zfxxgkpt/zhengce/gfxwj/"


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

        container = soup.find('div', class_='lzyj_list')
        if not container:
            container = soup.find('div', class_='gkzd_list')
        if not container:
            container = soup.find('div', class_='overview_right')

        if not container:
            print('[ERROR] 退役军人事务部规范性文件爬虫：未找到目标容器')
            return policies, all_items

        ul_element = container.find('ul')
        if not ul_element:
            print('[ERROR] 退役军人事务部规范性文件爬虫：未找到列表 ul')
            return policies, all_items

        li_elements = ul_element.find_all('li')
        if not li_elements:
            print('[ERROR] 退役军人事务部规范性文件爬虫：列表为空')
            return policies, all_items

        filtered_count = 0

        for li in li_elements:
            try:
                a_tag = li.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip()
                if not title:
                    title = a_tag.get_text(strip=True).replace('\n', '').strip()

                href = a_tag.get('href', '').strip()

                if not title or not href:
                    continue

                article_url = href
                if not article_url.startswith('http'):
                    if article_url.startswith('/'):
                        article_url = "https://www.mva.gov.cn" + href
                    elif article_url.startswith('./'):
                        article_url = "https://www.mva.gov.cn/gongkai/zfxxgkpt/zhengce/gfxwj/" + href[2:]
                    else:
                        article_url = "https://www.mva.gov.cn/" + href

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

                    content_elem = detail_soup.find('div', id='zhengwen')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='gkzn_zw')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='scroll_cont')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='TRS_UEDITOR')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='trs_editor_view')

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
                    'source': '退役军人事务部规范性文件'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] 退役军人事务部规范性文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 退役军人事务部规范性文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "退役军人事务部_规范性文件")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f'[DB] 写入数据库: {len(result)} 条')
            print("----------------------------------------")
            print("[OK] 爬虫 退役军人事务部规范性文件 执行成功")
            return result
        else:
            print("[DB] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return data
    except Exception as e:
        print(f'[ERROR] 爬虫 退役军人事务部规范性文件 运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()