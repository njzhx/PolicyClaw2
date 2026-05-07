import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

# 目标网站URL - 江苏省国资委 政策文件
TARGET_URL = "https://jsgzw.jiangsu.gov.cn/col/col85683/index.html"
SOURCE_NAME = "江苏省国资委_政策文件"

# ==========================================
# 1. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取数据，返回与表结构一致的字典列表"""
    policies = []
    all_items = 0
    
    try:
        # 计算前一天日期（北京时间 UTC+8）
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        datastore_script = next((s.string for s in soup.find_all('script') if s.string and '<datastore>' in s.string), None)
        
        if not datastore_script:
            print("❌ 未找到datastore脚本标签")
            return policies, all_items
        
        records = re.findall(r'<record><!\[CDATA\[(.*?)\]\]></record>', datastore_script, re.DOTALL)
        all_items = len(records)
        print(f"📋 找到 {all_items} 条政策文件")
        
        target_date_items = 0
        non_target_date_items = 0
        
        for record in records:
            title_match = re.search(r'title=(["\'])(.*?)\1', record)
            url_match = re.search(r'href=(["\'])(.*?)\1', record)
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', record)
            
            if not all([title_match, url_match, date_match]):
                continue
            
            title = title_match.group(2)
            url = url_match.group(2)
            date_str = date_match.group(1)
            
            # 解析日期
            try:
                pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                continue
            
            # 【严格匹配日期】
            if pub_at == yesterday:
                target_date_items += 1
                if not url.startswith('http'):
                    url = f"https://jsgzw.jiangsu.gov.cn{url}" if url.startswith('/') else f"https://jsgzw.jiangsu.gov.cn/{url}"
                
                # 抓取详情
                content = ""
                try:
                    d_res = requests.get(url, headers=headers, timeout=15)
                    d_res.encoding = d_res.apparent_encoding
                    d_soup = BeautifulSoup(d_res.text, 'html.parser')
                    # 匹配 .main-txt 或 #zoom
                    c_elem = d_soup.select_one('.main-txt') or d_soup.select_one('#zoom')
                    if c_elem:
                        # 移除不必要标签
                        for extra in c_elem.select('.main-word, .printer, script, style'):
                            extra.decompose()
                        content = c_elem.get_text(strip=True)
                        content = re.sub(r'浏览次数：.*$|来源：.*$', '', content, flags=re.MULTILINE)
                except Exception as e:
                    print(f"⚠️  抓取详情页失败: {url} - {e}")
                
                policies.append({
                    'title': title,
                    'url': url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '政策文件',
                    'source': SOURCE_NAME
                })
            else:
                non_target_date_items += 1
        
        # 统一输出格式：统计结果
        print(f"✅ 成功抓取昨日数据：{target_date_items} 条")
        print(f"⏭️  过滤非昨日数据：{non_target_date_items} 条")
        
        # 统一输出格式：打印最新5条
        print("\n📊 页面最新5条：")
        for record in records[:5]:
            t_m = re.search(r'title=(["\'])(.*?)\1', record)
            d_m = re.search(r'(\d{4}-\d{2}-\d{2})', record)
            if t_m and d_m:
                print(f"✅ {t_m.group(2)} [{d_m.group(1)}]")
        
    except Exception as e:
        print(f"❌ 抓取失败：{e}")
    
    return policies, all_items

# ==========================================
# 2. 数据入库逻辑
# ==========================================
def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, SOURCE_NAME)
    except Exception as e:
        return data_list, None

# ==========================================
# 3. 主函数
# ==========================================
def run():
    try:
        data, all_items = scrape_data()
        if data:
            result, _ = save_to_supabase(data)
            print(f"\n💾 写入数据库：{len(result)} 条")
            return result
        else:
            print("\n💾 写入数据库：0 条")
            print("⚠️  未找到昨日发布的政策文件")
            return []
    except Exception as e:
        print(f"❌ 运行失败：{e}")
        return []

if __name__ == "__main__":
    run()
