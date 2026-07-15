import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
from urllib.parse import urljoin
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

CRAWLER_CONFIGS = [
    {
        'name': '财政部经济建设司_通知公告',
        'url': 'https://jjs.mof.gov.cn/tongzhigonggao/',
        'source': '财政部经济建设司_通知公告'
    },
    {
        'name': '财政部经济建设司_政策法规',
        'url': 'https://jjs.mof.gov.cn/zhengcefagui/',
        'source': '财政部经济建设司_政策法规'
    },
    {
        'name': '财政部农业农村司_政策发布',
        'url': 'https://nys.mof.gov.cn/czpjZhengCeFaBu_2_2/',
        'source': '财政部农业农村司_政策发布'
    },
    {
        'name': '财政部社会保障司_工作动态',
        'url': 'https://sbs.mof.gov.cn/gongzuodongtai/',
        'source': '财政部社会保障司_工作动态'
    },
    {
        'name': '财政部科教和文化司_工作动态',
        'url': 'https://jkw.mof.gov.cn/gongzuodongtai/',
        'source': '财政部科教和文化司_工作动态'
    },
    {
        'name': '财政部科教和文化司_工作通知',
        'url': 'https://jkw.mof.gov.cn/gongzuotongzhi/',
        'source': '财政部科教和文化司_工作通知'
    },
    {
        'name': '财政部科教和文化司_政策发布',
        'url': 'https://jkw.mof.gov.cn/zhengcefabu/',
        'source': '财政部科教和文化司_政策发布'
    }
]


def scrape_single_config(config):
    policies = []
    all_items = []
    url = config['url']
    source_name = config['name']

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)

        for retry in range(3):
            try:
                response = requests.get(url, headers=headers, timeout=45)
                response.raise_for_status()
                break
            except Exception:
                if retry == 2:
                    raise
                time.sleep(1)
        soup = BeautifulSoup(response.content, 'html.parser')

        ul_element = soup.find('ul', class_='liBox')
        if not ul_element:
            print(f'[ERROR] {source_name}：未找到目标列表 ul.liBox')
            return policies, all_items

        li_elements = ul_element.find_all('li')
        if not li_elements:
            print(f'[ERROR] {source_name}：列表为空')
            return policies, all_items

        filtered_count = 0

        for li in li_elements:
            try:
                a_tag = li.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip()
                if not title:
                    title = a_tag.get_text(strip=True)

                href = a_tag.get('href', '').strip()

                if not title or not href:
                    continue

                article_url = href
                if not article_url.startswith('http'):
                    article_url = urljoin(url, href)

                pub_at = None
                span_tags = li.find_all('span')
                for span in span_tags:
                    span_text = span.get_text(strip=True)
                    date_match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', span_text)
                    if date_match:
                        try:
                            pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                            break
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
                    for retry in range(3):
                        try:
                            detail_resp = requests.get(article_url, headers=headers, timeout=30)
                            detail_resp.raise_for_status()
                            break
                        except Exception:
                            if retry == 2:
                                raise
                            time.sleep(0.5)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    content_elem = detail_soup.find('div', class_='TRS_Editor')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='my_doccontent')
                    if not content_elem:
                        content_elem = detail_soup.find('div', class_='my_conboxzw')
                    if content_elem:
                        text = content_elem.get_text(separator='\n', strip=True)
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        if lines:
                            content = '\n'.join(lines)

                except Exception as e:
                    pass

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': config['source']
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] {source_name}：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] {source_name}：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list, source_name):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, source_name)
    except Exception:
        return data_list


def create_runner(config):
    def runner():
        try:
            data, _ = scrape_single_config(config)
            result = save_to_supabase(data, config['name'])
            db_result = result[0] if isinstance(result, tuple) else result
            print(f'[DB] 写入数据库: {len(db_result)} 条')
            print("----------------------------------------")
            print(f"[OK] 爬虫 {config['name']} 执行成功")
            return db_result
        except Exception as e:
            print(f'[ERROR] 爬虫 {config["name"]} 运行失败 - {e}')
            print("----------------------------------------")
            return []
    return runner


for config in CRAWLER_CONFIGS:
    fn_name = f"run_{config['name'].replace(' ', '_').replace('/', '_')}"
    globals()[fn_name] = create_runner(config)


if __name__ == "__main__":
    for config in CRAWLER_CONFIGS:
        print(f"\n{'='*60}")
        print(f"测试爬虫: {config['name']}")
        print(f"{'='*60}")
        scrape_single_config(config)
