import os
import sys
import re
import subprocess
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date

from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_utils import save_to_policy

TARGET_URL = "https://www.nmpa.gov.cn/xxgk/fgwj/index.html"


def scrape_data():
    policies = []
    url = TARGET_URL
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()
        print("[" + "日期" + "] 运行日期（北京时间）：" + str(today))
        print("[" + "目标" + "] 目标抓取日期：" + target_date_label)

        cmd = [
            'curl',
            '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            '-H', 'Accept-Language: zh-CN,zh;q=0.9',
            '-H', 'Referer: https://www.nmpa.gov.cn/',
            '-H', 'Cache-Control: no-cache',
            '-H', 'Pragma: no-cache',
            '-L',
            '--cookie-jar', 'cookies.txt',
            url
        ]

        print("[" + "调试" + "] 执行curl命令")
        result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode != 0:
            print("[" + "失败" + "] curl执行失败: " + result.stderr.decode('utf-8', errors='ignore'))
            return policies, all_items

        page_content = result.stdout.decode('utf-8', errors='ignore')
        print("[" + "调试" + "] 页面内容长度: " + str(len(page_content)))

        if len(page_content) < 100:
            print("[" + "警告" + "] 页面内容异常，可能被反爬拦截")
            return policies, all_items

        soup = BeautifulSoup(page_content, 'html.parser')

        news_list = []

        all_li = soup.find_all('li')
        for li in all_li:
            a_tag = li.find('a')
            if a_tag and a_tag.get('href'):
                href = a_tag.get('href')
                text = a_tag.get_text(strip=True)
                if text and len(text) > 5:
                    li_text = li.get_text()
                    has_date = re.search(r'\d{4}-\d{2}-\d{2}', li_text)
                    if has_date:
                        news_list.append(li)

        print("[" + "调试" + "] 通过日期模式找到 " + str(len(news_list)) + " 个列表项")

        if len(news_list) == 0:
            title_text = soup.title.string if soup.title else '无标题'
            print("[" + "调试" + "] 页面标题: " + title_text)
            print("[" + "调试" + "] 页面内容预览（前3000字符）: " + page_content[:3000])

        filtered_count = 0

        for item in news_list:
            try:
                title_tag = item.find('a')
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                if not title:
                    continue

                if len(title) < 5:
                    continue

                policy_url = title_tag.get('href', '')
                if not policy_url:
                    continue

                if 'javascript' in policy_url.lower():
                    continue

                if policy_url.startswith('//'):
                    policy_url = 'https:' + policy_url
                elif not policy_url.startswith('http'):
                    if policy_url.startswith('/'):
                        policy_url = 'https://www.nmpa.gov.cn' + policy_url
                    else:
                        policy_url = 'https://www.nmpa.gov.cn/xxgk/fgwj/' + policy_url

                date_str = ''
                item_text = item.get_text(strip=True)

                date_patterns = [
                    r'(\d{4}-\d{2}-\d{2})',
                    r'(\d{4}/\d{2}/\d{2})',
                    r'(\d{4}年\d{1,2}月\d{1,2}日)'
                ]
                for pattern in date_patterns:
                    match = re.search(pattern, item_text)
                    if match:
                        date_str = match.group(1)
                        break

                pub_at = None
                if date_str:
                    date_str = date_str.strip()
                    try_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日', '%Y-%m-%d %H:%M:%S']
                    for fmt in try_formats:
                        try:
                            pub_at = datetime.strptime(date_str, fmt).date()
                            break
                        except ValueError:
                            continue

                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = ""
                try:
                    detail_cmd = [
                        'curl',
                        '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        '-H', 'Accept-Language: zh-CN,zh;q=0.9',
                        '-H', 'Referer: ' + url,
                        '-L',
                        '--cookie', 'cookies.txt',
                        policy_url
                    ]

                    detail_result = subprocess.run(detail_cmd, capture_output=True, timeout=30)
                    if detail_result.returncode == 0:
                        detail_content = detail_result.stdout.decode('utf-8', errors='ignore')
                        detail_soup = BeautifulSoup(detail_content, 'html.parser')

                        content_selectors = [
                            'div.content',
                            'div.article-content',
                            'div.main-content',
                            'div.TRS_Editor',
                            'div.detail-content',
                            'article',
                            '#content',
                            'div.text_content',
                            'div.content-text'
                        ]

                        content_elem = None
                        for selector in content_selectors:
                            content_elem = detail_soup.select_one(selector)
                            if content_elem:
                                break

                        if content_elem:
                            content = content_elem.get_text(strip=True)
                except Exception as e:
                    print("[" + "警告" + "] 获取详情页失败: " + policy_url + " - " + str(e))

                policy_data = {
                    'title': title,
                    'url': policy_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '国家药品监督管理局'
                }

                policies.append(policy_data)

            except Exception as e:
                print("[" + "警告" + "] 处理单条数据失败 - " + str(e))
                continue

        print("[" + "成功" + "] 国家药监局爬虫：成功抓取 " + str(len(policies)) + " 条目标日期窗口数据")
        print("[" + "过滤" + "] 过滤掉 " + str(filtered_count) + " 条非目标日期的数据")

        if all_items:
            print("[" + "统计" + "] 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print("[" + "成功" + "] " + item['title'] + " " + date_str)

    except Exception as e:
        print("[" + "失败" + "] 国家药监局爬虫：抓取失败 - " + str(e))

    return policies, all_items


def save_to_supabase(data_list):
    return save_to_policy(data_list, "国家药品监督管理局")


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print("[" + "写入" + "] 写入数据库: " + str(len(data)) + " 条")
            print("----------------------------------------")
            print("[" + "成功" + "] 爬虫 国家药品监督管理局 执行成功")
            return result
        else:
            print("[" + "写入" + "] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[" + "警告" + "] 未找到目标日期的文章")
            return data
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='replace').decode('utf-8')
        print("[" + "失败" + "] 爬虫 国家药品监督管理局 运行失败 - " + error_msg)
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
