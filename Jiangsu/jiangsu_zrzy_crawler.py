import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

# 目标网站URL
TARGET_URL = "https://zrzy.jiangsu.gov.cn/gtxxgk/nrglIndex.action?classID=2c9082548ad381c5018ad4bbd9a100ae"
SOURCE_NAME = "江苏省自然资源厅_政策文件"

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
        print(f"运行日期（北京时间）：{today}")
        print(f"目标抓取日期：{yesterday}")
        
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
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 查找政策文件列表
        # 分析页面结构，找到包含政策文件的容器
        # 根据用户提示，文章列表在 //*[contains(@class, "loat")]/div[1] 里
        
        # 尝试找到包含政策文件的列表
        policy_links = []
        
        # 使用BeautifulSoup查找包含政策文件的容器
        # 查找class包含"loat"的元素
        float_containers = soup.find_all(class_=lambda x: x and 'loat' in x)
        
        if float_containers:
            # 获取第一个div子元素
            article_container = float_containers[0].find('div')
            if article_container:
                # 查找容器中的所有链接
                for link in article_container.find_all('a'):
                    href = link.get('href')
                    text = link.get_text(strip=True)
                    if href and text and len(text) > 10:
                        # 即使包含nrglIndex.action，也添加到政策链接中
                        # 后续会处理这些链接
                        policy_links.append((text, href))
        
        # 如果通过float容器没有找到，回退到原来的方法
        if not policy_links:
            # 查找所有链接，筛选出政策文件链接
            for link in soup.find_all('a'):
                href = link.get('href')
                text = link.get_text(strip=True)
                if href and text and len(text) > 10:
                    # 检查链接是否是政策文件详情页
                    if 'nrglIndex.action' not in href and 'classID' not in href:
                        policy_links.append((text, href))
        
        all_items = len(policy_links)
        print(f"找到 {all_items} 条政策文件")
        
        target_date_items = 0
        non_target_date_items = 0
        
        for title, href in policy_links:
            # 从标题中提取日期
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', title)
            
            if not date_match:
                # 如果标题中没有日期，可能日期在其他位置
                # 尝试从页面中查找日期信息
                # 这里需要根据实际页面结构调整
                non_target_date_items += 1
                continue
            
            date_str = date_match.group(1)
            
            # 解析日期
            try:
                pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                non_target_date_items += 1
                continue
            
            # 只保留昨天的
            if pub_at != yesterday:
                non_target_date_items += 1
                continue
            
            # 处理URL
            if not href.startswith('http'):
                href = f"https://zrzy.jiangsu.gov.cn{href}" if href.startswith('/') else f"https://zrzy.jiangsu.gov.cn/{href}"
            
            # 抓取正文
            content = ""
            try:
                resp = requests.get(href, headers=headers, timeout=15)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding
                ds = BeautifulSoup(resp.text, 'html.parser')
                
                # 兼容常见内容容器
                content_elem = ds.select_one('.content') or ds.select_one('.main-content') or ds.select_one('.article-content')
                if content_elem:
                    # 移除无关代码
                    for extra in content_elem.select('script, style'):
                        extra.decompose()
                    content = content_elem.get_text(strip=True)
                    content = re.sub(r'来源：.*?$|浏览次数：.*?$', '', content, flags=re.MULTILINE).strip()
            except Exception as e:
                print(f"抓取详情失败：{href} | {e}")
            
            policy_data = {
                'title': title,
                'url': href,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '江苏省自然资源厅政策文件',
                'source': SOURCE_NAME
            }
            policies.append(policy_data)
            target_date_items += 1
        
        print(f"成功抓取昨日数据：{target_date_items} 条")
        print(f"过滤非昨日数据：{non_target_date_items} 条")
        
        # 显示实际的文件标题
        print("\n页面最新5条政策文件标题：")
        for i, (title, href) in enumerate(policy_links[:5]):
            try:
                # 尝试使用gbk编码打印标题
                print("第{}条: {}".format(i+1, title))
            except UnicodeEncodeError:
                try:
                    # 尝试使用utf-8编码打印标题
                    print("第{}条: {}".format(i+1, title.encode('utf-8').decode('utf-8')))
                except Exception:
                    # 如果仍然失败，只打印标题长度
                    print("第{}条: 标题长度 {}".format(i+1, len(title)))
        
    except Exception as e:
        print(f"抓取失败：{e}")
    
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
            print(f"\n写入数据库：{len(result)} 条")
            return result
        else:
            print("\n写入数据库：0 条")
            print("未找到昨日发布的政策文件")
            return []
    except Exception as e:
        print(f"爬虫运行失败：{e}")
        return []

# ==========================================
# 主入口
# ==========================================
if __name__ == "__main__":
    run()
