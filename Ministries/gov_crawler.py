
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from db_utils import save_to_policy

TARGET_URL = "https://www.gov.cn/zhengce/zuixin/"


def scrape_data():
    policies = []
    url = TARGET_URL
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        

        
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        ajax_url = None
        scripts = soup.find_all('script')
        for script in scripts:
            script_content = script.string
            if script_content and 'list-1-ajax-id' in script_content:
                import re
                patterns = [
                    r'url:\s*["\']([^"\']+)\.json["\']',
                    r'url:\s*["\']([^"\']+)\.json["\']',
                    r'ajax\s*:\s*["\']([^"\']+)["\']'
                ]
                for pattern in patterns:
                    ajax_match = re.search(pattern, script_content)
                    if ajax_match:
                        ajax_path = ajax_match.group(1)
                        if not ajax_path.endswith('.json'):
                            ajax_path += '.json'
                        if ajax_path.startswith('http'):
                            ajax_url = ajax_path
                        elif ajax_path.startswith('./'):
                            ajax_url = f"https://www.gov.cn/zhengce/zuixin/{ajax_path[2:]}"
                        else:
                            ajax_url = f"https://www.gov.cn/zhengce/zuixin/{ajax_path}"
                        break
                if ajax_url:
                    break
        
        if not ajax_url:
            ajax_url = "https://www.gov.cn/zhengce/zuixin/data.json"
        
        policy_items = []
        try:
            ajax_response = requests.get(ajax_url, timeout=15)
            if ajax_response.status_code == 200:
                import json
                data = ajax_response.json()
                if isinstance(data, list):
                    policy_items = data
        except Exception as e:
            print(f"⚠️  AJAX请求异常: {e}")
        
        filtered_count = 0
        
        for item in policy_items:
            if not isinstance(item, dict):
                continue
            
            title = item.get('TITLE', '')
            policy_url = item.get('URL', '')
            
            if not title or not policy_url:
                continue
            
            if not policy_url.startswith('http'):
                policy_url = f"https://www.gov.cn{policy_url}"
            
            date_str = item.get('DOCRELPUBTIME', '')
            pub_at = None
            if date_str:
                try:
                    pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    pass
            
            all_items.append({'title': title, 'pub_at': pub_at})
            
            if pub_at != yesterday:
                filtered_count += 1
                continue
            
            content = ""
            try:
                detail_response = requests.get(policy_url, timeout=15)
                detail_response.raise_for_status()
                detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
                content_elem = detail_soup.select_one('#UCAP-CONTENT')
                if content_elem:
                    content = content_elem.get_text(strip=True)
            except Exception:
                pass
            
            policy_data = {
                'title': title,
                'url': policy_url,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '',
                'source': '中国政府网'
            }
            
            policies.append(policy_data)
        
        print(f"✅ 中国政府网爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")
        
    except Exception as e:
        print(f"❌ 中国政府网爬虫：抓取失败 - {e}")
    
    return policies, all_items


def save_to_supabase(data_list):
    return save_to_policy(data_list, "中国政府网")


def run():
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 中国政府网爬虫：运行过程中发生未捕获的异常 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()

