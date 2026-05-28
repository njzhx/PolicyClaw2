
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://mzt.jiangsu.gov.cn/col/col78599/index.html"


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找目标容器
        target_div = soup.find('div', id='ztlist')
        if not target_div:
            print('[ERROR] 江苏省民政厅政策文件爬虫：未找到目标容器 div#ztlist')
            return policies, all_items

        # 查找iframe
        iframe = target_div.find('iframe')
        if not iframe:
            print('[ERROR] 江苏省民政厅政策文件爬虫：未找到 iframe')
            return policies, all_items

        iframe_src = iframe.get('src', '')
        if not iframe_src:
            print('[ERROR] 江苏省民政厅政策文件爬虫：iframe 没有 src 属性')
            return policies, all_items

        # 构建iframe的URL
        if not iframe_src.startswith('http'):
            iframe_url = "https://mzt.jiangsu.gov.cn" + iframe_src
        else:
            iframe_url = iframe_src

        # 访问iframe页面
        iframe_response = requests.get(iframe_url, headers=headers, timeout=15)
        iframe_soup = BeautifulSoup(iframe_response.content, 'html.parser')

        # 提取数据
        # 方法1：使用h4标题和a链接的配对
        h4_tags = iframe_soup.find_all('h4')
        all_links = iframe_soup.find_all('a', href=True)

        # 建立标题和链接的映射
        title_link_map = {}
        for a_tag in all_links:
            href = a_tag.get('href', '')
            # 只保留文章链接
            if href and ('art/' in href or '/art/' in href):
                text = a_tag.get_text(strip=True)
                if text and len(text) > 5:
                    title_link_map[text] = href

        filtered_count = 0

        for h4 in h4_tags:
            try:
                title = h4.get_text(strip=True)

                if not title or len(title) < 5:
                    continue

                # 查找对应的链接
                article_url = None
                for link_title, link_href in title_link_map.items():
                    # 简化标题用于匹配
                    simple_title = title.replace(' ', '').replace('\n', '')
                    simple_link_title = link_title.replace(' ', '').replace('\n', '')

                    if simple_title in simple_link_title or simple_link_title in simple_title:
                        article_url = link_href
                        break

                # 处理URL
                if article_url:
                    if article_url.startswith('/'):
                        article_url = "https://mzt.jiangsu.gov.cn" + article_url
                    elif article_url.startswith('http://mzt'):
                        article_url = article_url.replace('http://mzt', 'https://mzt')
                    elif not article_url.startswith('https'):
                        article_url = "https://mzt.jiangsu.gov.cn" + article_url

                # 提取日期
                pub_at = None
                next_ul = h4.find_next_sibling('ul')
                if next_ul:
                    li_items = next_ul.find_all('li')
                    for li in li_items:
                        li_text = li.get_text(strip=True)
                        if '发文日期' in li_text:
                            date_match = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', li_text)
                            if date_match:
                                try:
                                    pub_at = datetime.strptime(date_match.group(1), '%Y-%m-%d').date()
                                except ValueError:
                                    pass
                            break

                # 如果ul中没有，从链接路径提取日期
                if not pub_at and article_url:
                    date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', article_url)
                    if date_match:
                        try:
                            pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                        except ValueError:
                            pass

                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})

                # 过滤非目标日期
                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # 抓取详情页内容
                content = ""
                if article_url:
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
                    'url': article_url if article_url else '',
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '江苏省民政厅政策文件'
                }
                policies.append(policy_data)

            except Exception:
                continue

        print(f'[OK] 江苏省民政厅政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据')
        print(f'[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据')

        # 显示页面最新5条
        if all_items:
            print('[INFO] 页面最新5条是：')
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f'  {i}. {item["title"][:60]}... {date_str}')

    except Exception as e:
        print(f'[ERROR] 江苏省民政厅政策文件爬虫：抓取失败 - {e}')
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省民政厅_政策文件")
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
        print(f'[ERROR] 江苏省民政厅政策文件爬虫：运行失败 - {e}')
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
