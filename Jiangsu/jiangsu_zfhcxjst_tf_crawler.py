
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from db_utils import save_to_policy

TARGET_URL = "https://jsszfhcxjst.jiangsu.gov.cn/col/col8639/index.html"


def scrape_data(target_date=None):
    policies = []
    url = TARGET_URL
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        
        if target_date:
            try:
                yesterday = datetime.strptime(target_date, '%Y-%m-%d').date()
                print(f"📅 运行日期（北京时间）：{today}")
                print(f"🎯 目标抓取日期（手动指定）：{yesterday}")
            except ValueError:
                print(f"❌ 日期格式错误，使用前一天日期")
                yesterday = today - timedelta(days=1)
                print(f"📅 运行日期（北京时间）：{today}")
                print(f"🎯 目标抓取日期：{yesterday}")
        else:
            yesterday = today - timedelta(days=1)
            print(f"📅 运行日期（北京时间）：{today}")
            print(f"🎯 目标抓取日期：{yesterday}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
            'Connection': 'keep-alive',
            'Referer': 'https://jsszfhcxjst.jiangsu.gov.cn/col/col8639/index.html',
            'Origin': 'https://jsszfhcxjst.jiangsu.gov.cn'
        }
        
        ajax_url = "https://jsszfhcxjst.jiangsu.gov.cn/module/web/jpage/dataproxy.jsp"
        
        data = {
            'col': '1',
            'appid': '1',
            'webid': '34',
            'path': '/',
            'columnid': '8639',
            'sourceContentType': '1',
            'unitid': '286629',
            'webname': '江苏省住房和城乡建设厅',
            'permissiontype': '0',
            'startrecord': '1',
            'endrecord': '100',
            'perpage': '15'
        }
        
        print("🔍 调用AJAX接口获取数据...")
        ajax_response = requests.post(ajax_url, headers=headers, data=data, timeout=30)
        ajax_response.raise_for_status()
        
        import xml.etree.ElementTree as ET
        root = ET.fromstring(ajax_response.content)
        recordset = root.find('recordset')
        records = recordset.findall('record') if recordset is not None else []
        
        print(f"📋 找到 {len(records)} 条数据")
        
        filtered_count = 0
        
        for record in records:
            cdata = record.text
            if not cdata:
                continue
            
            item_soup = BeautifulSoup(cdata, 'html.parser')
            
            title_elem = item_soup.find('a')
            if not title_elem:
                continue
            
            title = title_elem.get_text(strip=True)
            policy_url = title_elem.get('href', '')
            
            if not title or not policy_url:
                continue
            
            if not policy_url.startswith('http'):
                if policy_url.startswith('/'):
                    policy_url = f"https://jsszfhcxjst.jiangsu.gov.cn{policy_url}"
                else:
                    policy_url = f"https://jsszfhcxjst.jiangsu.gov.cn/col/col8639/{policy_url}"
            
            date_elem = item_soup.find('span', class_='bt-right')
            date_str = date_elem.get_text(strip=True) if date_elem else ''
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
                detail_response = requests.get(policy_url, headers=headers, timeout=15)
                detail_response.raise_for_status()
                
                detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
                
                # 优先使用 .box_wzy_ys 类查找内容区域
                content_elem = detail_soup.select_one('.box_wzy_ys')
                
                # 如果找不到，尝试其他选择器
                if not content_elem:
                    content_elem = detail_soup.select_one('#zoom') or detail_soup.select_one('.main-fl-con')
                
                # 如果还是找不到，尝试查找包含大量文本的div
                if not content_elem:
                    divs = detail_soup.find_all('div')
                    for div in divs:
                        text = div.get_text(strip=True)
                        if text and len(text) > 500:
                            content_elem = div
                            break
                
                if content_elem:
                    content = content_elem.get_text(strip=True)
                    if len(content) > 0:
                        print(f"   📄 成功抓取到内容: 前80字符 = {content[:80]}...")
            except Exception as e:
                print(f"   ⚠️  抓取内容时出错: {e}")
                pass
            
            policy_data = {
                'title': title,
                'url': policy_url,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '',
                'source': '江苏省住房和城乡建设厅'
            }
            
            policies.append(policy_data)
        
        print(f"✅ 江苏省住房和城乡建设厅爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")
        
    except Exception as e:
        print(f"❌ 江苏省住房和城乡建设厅爬虫：抓取失败 - {e}")
    
    return policies, all_items


def save_to_supabase(data_list):
    return save_to_policy(data_list, "江苏省住房和城乡建设厅爬虫")


def run(target_date=None):
    try:
        data, _ = scrape_data(target_date)
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 江苏省住房和城乡建设厅爬虫：运行过程中发生未捕获的异常 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
        run(target_date)
    else:
        run()
