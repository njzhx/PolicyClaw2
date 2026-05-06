
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://gat.jiangsu.gov.cn/col/col59265/index.html"


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL

    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找容器
        container = soup.find('div', id='417912')
        if not container:
            print('[ERROR] 江苏省公安厅政策文件爬虫：未找到容器 417912')
            return policies, all_items

        # 查找 script 标签中的 XML 数据
        script = container.find('script')
        if not script or not script.string:
            print('[ERROR] 江苏省公安厅政策文件爬虫：未找到数据脚本')
            return policies, all_items

        script_content = script.string
        record_soup = BeautifulSoup(script_content, 'html.parser')
        records = record_soup.find_all('record')

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
                href = a_tag.get('href', '').strip()

                if not title:
                    continue

                # 处理URL
                if href.startswith('/'):
                    article_url = "https://gat.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://gat.jiangsu.gov.cn" + href
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

                # 如果span中没有，从链接路径提取日期
                if not pub_at:
                    date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', href)
                    if date_match:
                        try:
                            pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                        except ValueError:
                            pass

                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})

                # 过滤非目标日期
                if pub_at != yesterday:
                    filtered_count += 1
                    continue

                # 抓取详情页内容
                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    # 查找内容区域
                    content_div = detail_soup.select_one('.content')
                    if content_div:
                        text = content_div.get_text(separator='\n', strip=True)
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        content = '\n'.join(lines)

                    # 验证content是否爬取成功
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
                    'source': '江苏省公安厅政策文件'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] 江苏省公安厅政策文件爬虫：成功抓取 {len(policies)} 条前一天数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        # 显示页面最新5条
        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省公安厅政策文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省公安厅_政策文件")
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
        print(f'[ERROR] 江苏省公安厅政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
