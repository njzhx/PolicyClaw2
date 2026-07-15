import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Referer': 'https://www.cac.gov.cn/'
}

TARGET_URL = "https://www.cac.gov.cn/wxzw/zcfg/zcwj/A09370306index_1.htm"


def scrape_data():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()
        print(f"[DATE] 运行日期（北京时间）：{today}")
        print(f"[TARGET] 目标抓取日期：{target_date_label}")

        session = requests.Session()
        session.headers.update(headers)

        try:
            response = session.get("https://www.cac.gov.cn/", timeout=10)
        except:
            pass

        response = session.get(TARGET_URL, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.content, 'html.parser')

        side_column = soup.find('div', class_='side-right-column')
        if not side_column:
            print('[ERROR] 国家互联网信息办公室政策文件爬虫：未找到目标容器 div.side-right-column')
            return policies, all_items

        loading_info = side_column.find('div', id='loadingInfoPage')
        if not loading_info:
            print('[ERROR] 国家互联网信息办公室政策文件爬虫：未找到容器 div#loadingInfoPage')
            return policies, all_items

        li_elements = loading_info.find_all('li')
        if not li_elements:
            print('[ERROR] 国家互联网信息办公室政策文件爬虫：列表为空')
            return policies, all_items

        filtered_count = 0

        for li in li_elements:
            try:
                h5_tag = li.find('h5')
                times_div = li.find('div', class_='times')

                if not h5_tag or not times_div:
                    continue

                a_tag = h5_tag.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip()
                if not title:
                    title = a_tag.get_text(strip=True)
                    title = re.sub(r'\s+', ' ', title).strip()

                href = a_tag.get('href', '').strip()

                if not title or not href:
                    continue

                article_url = href
                if not article_url.startswith('http'):
                    if article_url.startswith('//'):
                        article_url = "https:" + href
                    elif article_url.startswith('/'):
                        article_url = "https://www.cac.gov.cn" + href
                    else:
                        article_url = "https://www.cac.gov.cn/" + href

                pub_at = None
                date_text = times_div.get_text(strip=True)
                date_match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', date_text)
                if date_match:
                    try:
                        pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                    except ValueError:
                        pass

                if not pub_at:
                    date_match = re.search(r'/(\d{4})[-](\d{2})[-](\d{2})/', href)
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
                    detail_resp = session.get(article_url, timeout=15)
                    detail_resp.encoding = detail_resp.apparent_encoding
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    content_elem = detail_soup.find('div', class_='main-content')
                    if not content_elem:
                        content_elem = detail_soup.find('div', id='BodyLabel')
                    if not content_elem:
                        content_elem = detail_soup.find('div', id='content1')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='content')
                    if not content_elem:
                        content_elem = detail_soup.find('article')
                    if not content_elem:
                        content_elem = detail_soup.find('body')

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
                    'category': '中央部委',
                    'source': '国家互联网信息办公室'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] 国家互联网信息办公室政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 国家互联网信息办公室政策文件爬虫：抓取失败 - {e}')
        import traceback
        traceback.print_exc()
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "国家互联网信息办公室")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        if data:
            result = save_to_supabase(data)
            print(f'[DB] 写入数据库: {len(result)} 条')
            print("----------------------------------------")
            print("[OK] 爬虫 国家互联网信息办公室政策文件 执行成功")
            return result
        else:
            print("[DB] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return data
    except Exception as e:
        print(f'[ERROR] 爬虫 国家互联网信息办公室政策文件 运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
