
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import time

# Selenium 为可选依赖
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
    SELENIUM_AVAILABLE = False

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

TARGET_URL = "https://www.miit.gov.cn/search/zcwjk.html?websiteid=110000000000000&pg=&p=&tpl=14&category=183&q="


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        # 使用前一天的日期
        yesterday = today - timedelta(days=1)
        # yesterday = datetime(2026, 2, 24).date()  # 测试有数据的日期
        
        filtered_count = 0
        
        # 1. 先获取分类信息
        category_api_url = "https://www.miit.gov.cn/search-front-server/api/structure/list-category"
        category_params = {
            "websiteid": "110000000000000",
            "searchid": "183"  # 从URL参数获取的category值
        }
        
        category_response = requests.get(category_api_url, params=category_params, headers=headers, timeout=30)
        
        cateid = "183"  # 默认值
        if category_response.status_code == 200:
            try:
                category_data = category_response.json()
                if category_data and 'data' in category_data and 'categories' in category_data['data']:
                    categories = category_data['data']['categories']
                    if categories:
                        cateid = categories[0].get('iid', '183')
            except Exception:
                pass
        
        # 2. 使用正确的分类ID进行搜索
        api_url = "https://www.miit.gov.cn/search-front-server/api/search/info"
        
        # 构建查询参数 - 基于search.js的分析
        # 不设置日期限制，获取所有数据后在本地筛选
        params = {
            "websiteid": "110000000000000",
            "scope": "basic",
            "q": "",  # 空搜索词，获取所有数据
            "pg": 50,  # 增加每页数量
            "p": 1,
            "cateid": cateid,
            "pos": "title_text,infocontent,titlepy",
            "_cus_eq_typename": "",  # 公文种类
            "_cus_eq_publishgroupname": "",  # 发布机构
            "_cus_eq_themename": "",  # 主题分类
            # 不设置日期限制，获取所有数据
            "dateField": "deploytime",
            "selectFields": "title,content,deploytime,_index,url,cdate,infoextends,infocontentattribute,columnname,filenumbername,publishgroupname,publishtime,metaid,bexxgk,columnid,xxgkextend1,xxgkextend2,themename,typename,indexcode,createdate",
            "group": "distinct",
            "highlightConfigs": "[{\"field\":\"infocontent\",\"numberOfFragments\":2,\"fragmentOffset\":0,\"fragmentSize\":30,\"noMatchSize\":145}]",
            "highlightFields": "title_text,infocontent,webid",
            "level": 6,
            "sortFields": "[{\"name\":\"deploytime\",\"type\":\"desc\"}]"
        }
        
        # 移除Content-Type头，使用默认的GET请求
        if 'Content-Type' in headers:
            del headers['Content-Type']
        response = requests.get(api_url, params=params, headers=headers, timeout=30)
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                # 处理API响应
                if data and 'data' in data and 'searchResult' in data['data']:
                    search_result = data['data']['searchResult']
                    
                    if 'dataResults' in search_result and search_result['dataResults']:
                        data_results = search_result['dataResults']
                        
                        for result in data_results:
                            try:
                                # 处理结果数据
                                if 'groupData' in result and result['groupData']:
                                    group_data = result['groupData'][0]['data']
                                else:
                                    group_data = result['data']
                                
                                title = group_data.get('title', '') or group_data.get('title_text', '')
                                url = group_data.get('url', '')
                                deploytime = group_data.get('deploytime', '')
                                
                                if not title or not url:
                                    continue
                                
                                # 构建完整URL
                                if url.startswith('/'):
                                    article_url = "https://www.miit.gov.cn" + url
                                else:
                                    article_url = url
                                
                                # 解析日期
                                pub_at = None
                                
                                # 优先使用jsearch_date字段（已经是字符串格式）
                                if 'jsearch_date' in group_data:
                                    jsearch_date = group_data.get('jsearch_date', '')
                                    if jsearch_date:
                                        try:
                                            pub_at = datetime.strptime(jsearch_date, '%Y-%m-%d').date()
                                        except ValueError:
                                            pass
                                
                                # 如果没有jsearch_date，尝试解析时间戳格式的日期
                                if not pub_at and deploytime:
                                    try:
                                        # 处理时间戳格式
                                        if isinstance(deploytime, str):
                                            # 尝试将时间戳转换为日期
                                            timestamp = int(deploytime) / 1000  # 毫秒转秒
                                            pub_at = datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8))).date()
                                    except (ValueError, TypeError):
                                        pass
                                
                                # 尝试其他日期字段
                                if not pub_at:
                                    # 尝试cdate字段
                                    cdate = group_data.get('cdate', '')
                                    if cdate:
                                        try:
                                            timestamp = int(cdate) / 1000
                                            pub_at = datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8))).date()
                                        except (ValueError, TypeError):
                                            pass
                                
                                # 保存到 all_items 用于显示最新5条
                                all_items.append({'title': title, 'pub_at': pub_at})
                                
                                if pub_at != yesterday:
                                    filtered_count += 1
                                    continue
                                
                                # 抓取内容
                                content = ""
                                try:
                                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                                    # 优先使用 #con_con，然后尝试其他选择器
                                    content_elem = detail_soup.select_one('#con_con') or detail_soup.select_one('.content') or detail_soup.select_one('#content') or detail_soup.select_one('.article-content') or detail_soup.select_one('.TRS_Editor')
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
                                    'source': '工信部'
                                }
                                policies.append(policy_data)
                                
                            except Exception:
                                continue
            except Exception:
                pass
        
        # 显示结果
        print(f"🎯 目标抓取日期：{yesterday}")
        print(f"✅ 工信部爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                # 控制标题长度为10个汉字
                title = item['title']
                if len(title) > 10:
                    title = title[:10] + "..."
                print(f"✅ {title} {date_str}")
        
    except Exception as e:
        print(f"❌ 工信部爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "工信部_文件库")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list


def run():
    try:
        #print("📦 开始执行爬虫: 工信部_文件库")
        #print(f"🔗 目标网址: `{TARGET_URL}`")
        #print("----------------------------------------")
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 工信部爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []


def run_test():
    """测试版本"""
    print("=" * 60)
    print("🧪 Testing MIIT File Library Crawler")
    print("=" * 60)
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        print(f"Date (Beijing): {today}")
        print(f"Target date: {yesterday}")
        
        # 测试直接搜索URL
        search_url = f"https://www.miit.gov.cn/search/zcwjk.html?websiteid=110000000000000&pg=10&p=1&tpl=14&category=183&q=&begin={yesterday}&end={yesterday}"
        print(f"Testing search URL: {search_url}")
        
        response = requests.get(search_url, headers=headers, timeout=30)
        print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            # 保存整个页面
            with open('miit_full_page.html', 'w', encoding='utf-8') as f:
                f.write(response.text)
            print("Saved full page to miit_full_page.html")
            
            soup = BeautifulSoup(response.content, 'html.parser')
            print(f"Page title: {soup.title.string}")
            
            # 查找搜索结果
            search_content = soup.find('div', class_='search-conent')
            if search_content:
                print("Found search content div")
                # 保存内容以便分析
                with open('miit_search_result.html', 'w', encoding='utf-8') as f:
                    f.write(str(search_content))
                print("Saved search content to miit_search_result.html")
            else:
                print("No search content found")
        
    except Exception as e:
        print(f"Test failed: {e}")


if __name__ == "__main__":
    # 默认运行正式版本
    run()
    # 如需运行测试版本，取消下面的注释
    # run_test()

