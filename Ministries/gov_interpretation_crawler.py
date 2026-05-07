import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# 导入数据库工具
from db_utils import save_to_policy

# 爬虫配置
TARGET_URL = "https://www.gov.cn/zhengce/jiedu/"

# ==========================================
# 2. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取中国政府网政策解读数据
    
    只抓取前一天发布的文章
    例如：运行时是2026年2月18日，只抓取2026年2月17日的文章
    
    Returns:
        tuple: (policies, all_items)
            - policies: 符合目标日期的数据列表
            - all_items: 所有抓取到的项目（用于显示最新5条）
    """
    policies = []
    url = TARGET_URL + "index.htm"
    all_items = []
    
    try:
        # 计算前一天日期（使用北京时间 UTC+8）
        from datetime import timezone
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        

        
        # 发送请求
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # 解析HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找JSON数据URL
        json_url = None
        scripts = soup.find_all('script')
        for script in scripts:
            script_content = script.string
            if script_content and 'list-1-ajax-id' in script_content:
                json_match = re.search(r'url:\s*["\']([^"\']+)ZCJD_QZ\.json["\']', script_content)
                if json_match:
                    json_path = json_match.group(1) + "ZCJD_QZ.json"
                    if json_path.startswith('./'):
                        json_url = f"https://www.gov.cn/zhengce/jiedu/{json_path[2:]}"
                    else:
                        json_url = f"https://www.gov.cn/zhengce/jiedu/{json_path}"
                    break
        
        # 访问JSON数据文件
        json_policies = []
        try:
            if not json_url:
                json_url = "https://www.gov.cn/zhengce/jiedu/ZCJD_QZ.json"
            
            response = requests.get(json_url, timeout=15)
            if response.status_code == 200:
                import json
                data = response.json()
                
                if isinstance(data, list):
                    # 筛选目标日期的文章
                    total_count = len(data)
                    filtered_count = 0
                    
                    for article in data:
                        if isinstance(article, dict) and 'TITLE' in article and 'URL' in article and 'DOCRELPUBTIME' in article:
                            try:
                                pub_at = datetime.strptime(article['DOCRELPUBTIME'], '%Y-%m-%d').date()
                                
                                # 保存到 all_items 用于显示最新5条
                                all_items.append({'title': article['TITLE'], 'pub_at': pub_at})
                                
                                if pub_at == yesterday:
                                    # 获取文章URL
                                    article_url = article['URL'] if article['URL'].startswith('http') else f"https://www.gov.cn{article['URL']}"
                                    
                                    # 抓取详情页内容
                                    content = ""
                                    try:
                                        detail_response = requests.get(article_url, timeout=15)
                                        detail_response.raise_for_status()
                                        detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
                                        # 使用用户提供的XPath对应的CSS选择器
                                        content_elem = detail_soup.select_one('#UCAP-CONTENT')
                                        if content_elem:
                                            content = content_elem.get_text(strip=True)
                                    except Exception:
                                        pass
                                    
                                    policy_data = {
                                        'title': article['TITLE'],
                                        'url': article_url,
                                        'pub_at': pub_at,
                                        'content': content,
                                        'selected': False,
                                        'category': '',
                                        'source': '中国政府网政策解读'
                                    }
                                    json_policies.append(policy_data)
                                else:
                                    filtered_count += 1
                            except Exception:
                                filtered_count += 1
                                pass
                    
                    print(f"✅ 中国政府网政策解读爬虫：成功抓取 {len(json_policies)} 条前一天数据")
                    print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
                    
                    # 显示页面最新5条
                    if all_items:
                        print("📊 页面最新5条是：")
                        for i, item in enumerate(all_items[:5], 1):
                            date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                            print(f"✅ {item['title']} {date_str}")
                    
                    policies = json_policies
        except Exception as e:
            print(f"⚠️  访问JSON文件失败：{e}")
        
    except Exception as e:
        print(f"❌ 中国政府网政策解读爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items

# ==========================================
# 3. 数据入库逻辑
# ==========================================
def save_to_supabase(data_list):
    """保存数据到数据库
    
    使用统一的数据库工具函数
    """
    return save_to_policy(data_list, "中国政府网政策解读")

# ==========================================
# 主函数
# ==========================================
def run():
    """运行中国政府网政策解读爬虫"""
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 中国政府网政策解读爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []

if __name__ == "__main__":
    run()
