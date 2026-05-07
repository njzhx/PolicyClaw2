import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

# 导入数据库工具
from db_utils import save_to_policy

# 爬虫配置
TARGET_URL = "https://wap.miit.gov.cn/jgsj/xgj/wjfb/index.html"
API_URL = "https://wap.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit"
API_PARAMS = {
    'parseType': 'buildstatic',
    'webId': '8d828e408d90447786ddbe128d495e9e',
    'tplSetId': '209741b2109044b5b7695700b2bec37e',
    'pageType': 'column',
    'tagId': '当前栏目_list',
    'editType': 'null',
    'pageId': 'f0440cd762ec4f9193f847902f957bb4'
}
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# ==========================================
# 1. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取工信部信息通信管理局文件发布数据
    
    只抓取前一天发布的文章
    例如：运行时是2026年3月4日，只抓取2026年3月3日的文章
    
    Returns:
        tuple: (policies, all_items)
            - policies: 符合目标日期的数据列表
            - all_items: 所有抓取到的项目（用于显示最新5条）
    """
    policies = []
    all_items = []
    
    try:
        # 计算前一天日期（使用北京时间 UTC+8）
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        print(f"📅 运行日期（北京时间）：{today}")
        print(f"🎯 目标抓取日期：{yesterday}")
        
        # 发送API请求
        response = requests.get(API_URL, headers=HEADERS, params=API_PARAMS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找文章列表
        article_list = soup.find_all('li')
        print(f"📋 找到 {len(article_list)} 条数据")
        
        filtered_count = 0
        
        for article in article_list:
            try:
                a_tag = article.find('a')
                if not a_tag:
                    continue
                
                title = a_tag.get_text(strip=True)
                href = a_tag.get('href')
                date_span = article.find('span')
                date_str = date_span.get_text(strip=True) if date_span else ''
                
                if not title or not href:
                    continue
                
                # 清理链接
                href = href.replace('"', '')
                
                # 构建完整URL
                if href.startswith('/'):
                    article_url = f"https://wap.miit.gov.cn{href}"
                elif not href.startswith('http'):
                    article_url = f"https://wap.miit.gov.cn{href}"
                else:
                    article_url = href
                
                # 解析日期
                pub_at = None
                if date_str:
                    try:
                        pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass
                
                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})
                
                # 过滤：只保留目标日期的文章
                if pub_at != yesterday:
                    filtered_count += 1
                    continue
                
                # 抓取详情页内容
                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=HEADERS, timeout=15)
                    detail_resp.raise_for_status()
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                    
                    # 查找内容区域
                    content_div = None
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
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '工信部信息通信管理局文件发布'
                }
                
                policies.append(policy_data)
                
            except Exception as e:
                print(f"⚠️  单条数据处理失败 - {e}")
                continue
        
        print(f"\n✅ 工信部信息通信管理局文件发布爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        # 显示页面最新5条
        if all_items:
            print("\n📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title'][:50]}... {date_str}")
        
    except Exception as e:
        print(f"❌ 工信部信息通信管理局文件发布爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items

# ==========================================
# 2. 数据入库逻辑
# ==========================================
def save_to_supabase(data_list):
    """保存数据到数据库
    
    使用统一的数据库工具函数
    """
    return save_to_policy(data_list, "工信部信息通信管理局文件发布")

# ==========================================
# 3. 主函数
# ==========================================
def run():
    """运行工信部信息通信管理局文件发布爬虫"""
    try:
        print("🔍 开始执行爬虫: 工信部信息通信管理局文件发布")
        print("----------------------------------------")
        data, _ = scrape_data()
        if data:
            result = save_to_supabase(data)
            print(f"📊 抓取数据: {len(data)} 条")
            print(f"💾 写入数据库: {len(result)} 条")
            print("✅ 爬虫 工信部信息通信管理局文件发布 执行成功")
        else:
            print("⚠️  未找到目标日期的文章")
            print("✅ 爬虫 工信部信息通信管理局文件发布 执行完成")
        return data
    except Exception as e:
        print(f"❌ 爬虫 工信部信息通信管理局文件发布 运行失败 - {e}")
        return []

# ==========================================
# 主入口
# ==========================================
if __name__ == "__main__":
    run()
