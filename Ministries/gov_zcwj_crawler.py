import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

Headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary',
    'Origin': 'https://sousuo.www.gov.cn'
}

TARGET_URL = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?q=&t=zhengcelibrary&orpro="

API_URL = "https://sousuo.www.gov.cn/search-gov/data"
API_COOKIES = "_qimei_uuid42=19b0c0b313910000a4cf89a20e72d2bc27b92965c2; _qimei_i_3=7be76886c45e58d8c7c4af61528177e3f3efa4a7100d558ae7dc7e5e2f90226b356663943c89e2bd8084; _qimei_h38=aea3debfa4cf89a20e72d2bc02000000819b0c; wdcid=0c788098375b7e28; __auc=053a196d19bd558a9d02fc6b252; _qimei_i_1=7fcd64d3c00b538f94c5a8615fd725e8febfa6f1475c01d6b6dd7b582493206c6163379d3980b0dc85b7f3e4; _qimei_fingerprint=933d898aca3f979f69c8525dc88033dd; arialoadData=false; ariauseGraymode=false"

CATEGORY_MAP = {
    'gongwen': '国务院文件',
    'bumenfile': '国务院部门文件',
    'otherfile': '其他文件',
    'gongbao': '国务院公报'
}


def get_api_session():
    session = requests.Session()
    
    main_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    
    session.get(TARGET_URL, headers=main_headers, timeout=30)
    
    for cookie in API_COOKIES.split('; '):
        if '=' in cookie:
            name, value = cookie.split('=', 1)
            session.cookies.set(name.strip(), value.strip())
    
    return session


def scrape_with_api():
    try:
        session = get_api_session()
        params = {
            't': 'zhengcelibrary',
            'q': '',
            'p': '1',
            'n': '200',
            'type': 'gwyzcwjk'
        }
        response = session.get(API_URL, headers=Headers, params=params, timeout=30)
        data = response.json()
        searchVO = data.get('searchVO', {})
        catMap = searchVO.get('catMap', {})
        
        all_items = []
        
        for catKey, catData in catMap.items():
            catName = CATEGORY_MAP.get(catKey, catKey)
            listVO = catData.get('listVO', [])
            
            for item in listVO:
                title_raw = item.get('title', '')
                title = re.sub(r'</?em>', '', title_raw)
                title = re.sub(r'<br\s*/?>', ' ', title)
                
                date_str = item.get('pubtimeStr', '')
                pub_at = None
                if date_str:
                    try:
                        pub_at = datetime.strptime(date_str, '%Y.%m.%d').date()
                    except ValueError:
                        pass
                
                pcode = item.get('pcode', '')
                url = item.get('sourcelink', item.get('url', ''))
                
                if not url:
                    continue
                
                all_items.append({
                    'title': title,
                    'url': url,
                    'pub_at': pub_at,
                    'category': catName,
                    'pcode': pcode
                })
        
        return all_items
    except Exception as e:
        print(f"API fetch error: {e}")
        import traceback
        traceback.print_exc()
        return []


def scrape_data():
    policies = []
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        print(f"运行日期（北京时间）：{today}")
        print(f"目标抓取日期：{yesterday}")
        
        print("正在从API获取数据...")
        all_items = scrape_with_api()
        
        print(f"API返回 {len(all_items)} 条数据")
        
        filtered_count = 0
        
        for item in all_items:
            try:
                title = item.get('title', '')
                href = item.get('url', '')
                pub_at = item.get('pub_at')
                category = item.get('category', '')
                
                if not title or not href:
                    continue
                
                if not href.startswith('http'):
                    href = f"https://sousuo.www.gov.cn{href}"
                
                if pub_at != yesterday:
                    filtered_count += 1
                    continue
                
                content = ""
                try:
                    detail_resp = requests.get(href, headers=Headers, timeout=15)
                    detail_resp.raise_for_status()
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                    
                    content_table = detail_soup.find('table', class_='border-table noneBorder pages_content')
                    if content_table:
                        content = content_table.get_text(separator='\n', strip=True)
                    else:
                        content_elem = detail_soup.select_one('#UCAP-CONTENT') or detail_soup.select_one('.article-content')
                        if content_elem:
                            content = content_elem.get_text(separator='\n', strip=True)
                        else:
                            max_text = ""
                            for tag in detail_soup.find_all(['div', 'td', 'p']):
                                text = tag.get_text(strip=True)
                                if len(text) > len(max_text):
                                    max_text = text
                            content = max_text
                except Exception as e:
                    print(f"抓取详情页失败：{e}")
                
                policy_data = {
                    'title': title,
                    'url': href,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': category,
                    'source': '国务院文件'
                }
                
                policies.append(policy_data)
                
            except Exception as e:
                print(f"单条数据处理失败 - {e}")
                continue
        
        print(f"\n国务院文件爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"过滤掉 {filtered_count} 条非目标日期的数据")
        
        if all_items:
            print(f"\n页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title_clean = re.sub(r'\s+', ' ', item['title'])
                print(f"{title_clean[:50]}... {date_str}")
        
    except Exception as e:
        print(f"国务院文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "国务院文件")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n写入数据库: {len(result)} 条")
            print("----------------------------------------")
            print("爬虫 国务院文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n写入数据库: 0 条")
            print("----------------------------------------")
            print("未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"爬虫 国务院文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
