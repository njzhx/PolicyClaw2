import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

# 目标网站URL
TARGET_URL = "https://kxjst.jiangsu.gov.cn/col/col82571/index.html"
SOURCE_NAME = "江苏省科学技术厅_政策文件"

# ==========================================
# 1. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取数据，返回与表结构一致的字典列表"""
    policies = []
    all_items = 0
    
    try:
        # 计算前一天日期（使用北京时间 UTC+8）
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        # 请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }
        
        # 请求页面
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8' # 科技厅通常是utf-8
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 【修改点1】：重新使用 datastore 解析逻辑
        datastore_script = next((s.string for s in soup.find_all('script') if s.string and '<datastore>' in s.string), "")
        records = re.findall(r'<record><!\[CDATA\[(.*?)\]\]></record>', datastore_script, re.DOTALL)
        
        all_items = len(records)
        print(f"📋 找到 {all_items} 条政策文件")
        
        target_date_items = 0
        non_target_date_items = 0
        
        for record in records:
            # 【修改点2】：使用正则提取标题、链接和日期
            title_match = re.search(r'title=(["\'])(.*?)\1', record)
            url_match = re.search(r'href=(["\'])(.*?)\1', record)
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', record)
            
            if not all([title_match, url_match, date_match]):
                continue
                
            title = title_match.group(2)
            href = url_match.group(2)
            date_str = date_match.group(1)
            
            # 解析日期
            try:
                pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                continue
            
            # 只保留昨天的
            if pub_at != yesterday:
                non_target_date_items += 1
                continue
            
            # 处理URL
            if not href.startswith('http'):
                href = f"https://kxjst.jiangsu.gov.cn{href}" if href.startswith('/') else f"https://kxjst.jiangsu.gov.cn/{href}"
            
            # 抓取正文
            content = ""
            try:
                resp = requests.get(href, headers=headers, timeout=15)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding
                ds = BeautifulSoup(resp.text, 'html.parser')
                
                # 兼容常见内容容器
                content_elem = ds.select_one('.main-txt') or ds.select_one('#zoom') or ds.select_one('.bt-content')
                if content_elem:
                    # 移除无关代码
                    for extra in content_elem.select('script, style'):
                        extra.decompose()
                    content = content_elem.get_text(strip=True)
                    content = re.sub(r'来源：.*?$|浏览次数：.*?$', '', content, flags=re.MULTILINE).strip()
            except Exception as e:
                print(f"⚠️  抓取详情失败：{href} | {e}")
            
            policy_data = {
                'title': title,
                'url': href,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '江苏省科技厅政策文件',
                'source': SOURCE_NAME
            }
            policies.append(policy_data)
            target_date_items += 1
        
        print(f"✅ 成功抓取昨日数据：{target_date_items} 条")
        print(f"⏭️  过滤非昨日数据：{non_target_date_items} 条")
        
        # 【修改点3】：根据 records 打印最新5条
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
    except Exception:
        return data_list, None

# ==========================================
# 3. 主函数
# ==========================================
def run():
    try:
        data, all_items = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n💾 写入数据库：{len(result)} 条")
            return result
        else:
            print("\n💾 写入数据库：0 条")
            print("⚠️  未找到昨日发布的政策文件")
            return []
    except Exception as e:
        print(f"❌ 爬虫运行失败：{e}")
        return []

# ==========================================
# 主入口
# ==========================================
if __name__ == "__main__":
    run()
