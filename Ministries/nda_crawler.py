import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

from crawler_core import format_date_window, get_crawl_date_window, is_target_date

# 导入数据库工具
from db_utils import save_to_policy

# 爬虫配置
TARGET_URL = "https://www.nda.gov.cn/sjj/zwgk/list/index_pc_1.html"


def scrape_data_test():
    """测试版本：抓取数据，不过滤日期，返回所有数据

    Returns:
        list: 抓取到的数据列表
    """
    policies = []
    url = TARGET_URL

    try:
        print(f"🔍 开始测试爬虫: 数据局_政务公开")
        print(f"🔗 目标网址: {url}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        # 发送请求
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # 解析HTML
        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找所有文章列表项
        items = soup.find_all('li')

        print(f"📦 找到 {len(items)} 个可能的列表项")

        for item in items:
            try:
                # 查找链接
                a_tag = item.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                href = a_tag.get('href', '')

                if not title or not href:
                    continue

                # 确保URL是完整的
                if href.startswith('/'):
                    article_url = f"https://www.nda.gov.cn{href}"
                elif not href.startswith('http'):
                    article_url = f"https://www.nda.gov.cn/sjj/zwgk/list/{href}"
                else:
                    article_url = href

                # 提取发布日期
                pub_at = None
                date_span = item.find('span')
                if date_span:
                    date_str = date_span.get_text(strip=True)
                    if date_str:
                        try:
                            # 尝试解析 YYYY.MM.DD 格式
                            pub_at = datetime.strptime(date_str, '%Y.%m.%d').date()
                        except ValueError:
                            pass

                print(f"📄 标题: {title}")
                print(f"   URL: {article_url}")
                print(f"   日期: {pub_at}")

                # 提取内容 - 抓取详情页内容
                content = ""
                try:
                    detail_response = requests.get(article_url, headers=headers, timeout=15)
                    detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
                    # 尝试查找内容区域
                    content_elem = detail_soup.select_one('.content') or detail_soup.select_one('#content') or detail_soup.select_one('.zwgk-content')
                    if content_elem:
                        content = content_elem.get_text(strip=True)
                        print(f"   内容长度: {len(content)} 字符")
                    else:
                        print(f"   ⚠️ 未找到内容元素")
                except Exception as e:
                    print(f"   ⚠️ 抓取详情页失败: {e}")

                # 构建政策数据
                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '国家数据局政务公开'
                }

                policies.append(policy_data)
                print("-" * 60)

            except Exception as e:
                print(f"⚠️ 处理单条数据失败: {e}")
                continue

        print(f"✅ 成功抓取 {len(policies)} 条数据")

    except Exception as e:
        print(f"❌ 爬虫失败 - {e}")

    return policies


def scrape_data():
    """正式版本：抓取数据，只抓取目标日期窗口发布的文章

    Returns:
        tuple: (policies, all_items)
            - policies: 符合目标日期的数据列表
            - all_items: 所有抓取到的项目（用于显示最新5条）
    """
    policies = []
    url = TARGET_URL
    all_items = []

    try:
        # 计算目标日期窗口日期（使用北京时间 UTC+8）
        from datetime import timezone
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)



        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        # 发送请求
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # 解析HTML
        soup = BeautifulSoup(response.content, 'html.parser')

        # 查找所有文章列表项
        items = soup.find_all('li')

        filtered_count = 0

        for item in items:
            try:
                a_tag = item.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                href = a_tag.get('href', '')

                if not title or not href:
                    continue

                # 确保URL是完整的
                if href.startswith('/'):
                    article_url = f"https://www.nda.gov.cn{href}"
                else:
                    article_url = href

                # 提取发布日期
                pub_at = None
                date_span = item.find('span')
                if date_span:
                    date_str = date_span.get_text(strip=True)
                    if date_str:
                        try:
                            pub_at = datetime.strptime(date_str, '%Y.%m.%d').date()
                        except ValueError:
                            pass

                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})

                # 过滤：只保留目标日期窗口的文章
                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                # 提取内容
                content = ""
                try:
                    detail_response = requests.get(article_url, headers=headers, timeout=15)
                    detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
                    content_elem = detail_soup.select_one('.content') or detail_soup.select_one('#content') or detail_soup.select_one('.zwgk-content')
                    if content_elem:
                        content = content_elem.get_text(strip=True)
                except Exception:
                    pass

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '国家数据局政务公开'
                }

                policies.append(policy_data)

            except Exception:
                continue

        print(f"✅ 国家数据局爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")

        # 显示页面最新5条
        if all_items:
                print("📊 页面最新5条是：")
                for i, item in enumerate(all_items[:5], 1):
                    date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                    print(f"✅ {item['title']} {date_str}")

    except Exception as e:
        print(f"❌ 国家数据局爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def save_to_supabase(data_list):
    return save_to_policy(data_list, "国家数据局")


def run():
    """运行爬虫"""
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 国家数据局爬虫：运行过程中发生未捕获的异常 - {e}")
        print("----------------------------------------")
        return []


def run_test():
    """测试爬虫"""
    print("=" * 60)
    print("🧪 开始测试爬虫")
    print("=" * 60)
    return scrape_data_test()


if __name__ == "__main__":
    # 默认运行测试版本
    run_test()

