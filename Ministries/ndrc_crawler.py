import os
import requests
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

from crawler_core import format_date_window, get_crawl_date_window, is_target_date

# 尝试导入 Selenium
SELENIUM_AVAILABLE = False
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    SELENIUM_AVAILABLE = True
except ImportError:
    print("Selenium 未安装，将尝试其他方法")

# 导入数据库工具
from db_utils import save_to_policy

# 爬虫配置
TARGET_URL = "https://www.ndrc.gov.cn/xxgk/wjk/"

# ==========================================
# 2. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取国家发改委文件库数据

    只抓取目标日期窗口发布的文章
    例如：运行时是2026年2月18日，只抓取2026年2月17日的文章

    Returns:
        tuple: (policies, all_items)
            - policies: 符合目标日期的数据列表
            - all_items: 所有抓取到的项目（用于显示最新5条）
    """
    policies = []
    all_items = []

    try:
        # 计算目标日期窗口日期（使用北京时间 UTC+8）
        from datetime import timezone
        # 创建 UTC+8 时区
        tz_utc8 = timezone(timedelta(hours=8))
        # 获取北京时间
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)

        # 直接调用 API 接口（从页面代码中提取的正确 API）
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        api_url = "https://fwfx.ndrc.gov.cn/api/query"

        # 构建正确的请求参数（从页面代码中提取）
        params = {
            'qt': '',  # 搜索关键词
            'tab': 'all',  # 所有文件类型
            'page': 1,  # 页码
            'pageSize': 50,  # 每页数量
            'siteCode': 'bm04000fgk',  # 站点代码
            'key': 'CAB549A94CF659904A7D6B0E8FC8A7E9',  # 密钥
            'startDateStr': '',  # 开始日期（空字符串表示不限制）
            'endDateStr': '',  # 结束日期（空字符串表示不限制）
            'timeOption': 0,  # 时间选项：0表示不限制
            'sort': 'dateDesc'  # 按日期降序排序
        }

        response = requests.get(api_url, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        # 解析 JSON
        import json
        data = response.json()

        items = []
        if data.get('ok', False):
            result_list = data.get('data', {}).get('resultList', [])
            for item in result_list:
                if isinstance(item, dict):
                    title = item.get('title', '')
                    href = item.get('url', '')
                    doc_date = item.get('docDate', '')

                    if title and href and doc_date:
                        # 提取日期部分
                        date_str = doc_date.split(' ')[0]
                        items.append((title, href, date_str))

        print(f"📋 找到 {len(items)} 条数据")
        filtered_count = 0

        for title, href, date_str in items:
            # 解析日期
            pub_at = None
            if date_str:
                try:
                    pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    pass

            # 构建完整URL
            policy_url = href
            if not policy_url.startswith('http'):
                if policy_url.startswith('/'):
                    policy_url = f"https://www.ndrc.gov.cn{policy_url}"
                else:
                    policy_url = f"https://www.ndrc.gov.cn/xxgk/wjk/{policy_url}"

            # 保存到 all_items 用于显示最新5条
            all_items.append({'title': title, 'pub_at': pub_at})

            # 过滤：只保留目标日期的文章
            if is_target_date(pub_at, target_date_from, target_date_to):
                # 抓取详情页内容
                content = ""
                try:
                    detail_resp = requests.get(policy_url, headers=headers, timeout=15)
                    detail_resp.raise_for_status()
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    # 使用XPath查找内容区域
                    # 注意：BeautifulSoup不直接支持XPath，我们使用CSS选择器来模拟
                    # XPath: //div[@class="article_con article_con_title"]
                    content_div = detail_soup.select_one('.article_con.article_con_title')

                    # 如果找不到特定的内容区域，尝试查找包含大量文本的div
                    if not content_div:
                        divs = detail_soup.find_all('div')
                        for div in divs:
                            text = div.get_text(strip=True)
                            if text and len(text) > 500:
                                content_div = div
                                break

                    if content_div:
                        content = content_div.get_text(strip=True)
                except Exception as e:
                    print(f"⚠️  抓取详情页失败：{e}")

                # 构建政策数据
                policy_data = {
                    'title': title,
                    'url': policy_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '国家发展和改革委员会发改委文件'
                }

                policies.append(policy_data)
            else:
                filtered_count += 1

        print(f"✅ 国家发改委爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")

        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")

    except Exception as e:
        print(f"❌ 国家发改委爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items

# ==========================================
# 3. 数据入库逻辑
# ==========================================
def save_to_supabase(data_list):
    """保存数据到数据库

    使用统一的数据库工具函数
    """
    return save_to_policy(data_list, "国家发改委")

# ==========================================
# 主函数
# ==========================================
def run():
    """运行国家发改委爬虫"""
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 国家发改委爬虫：运行过程中发生未捕获的异常 - {e}")
        print("----------------------------------------")
        return []

if __name__ == "__main__":
    run()
