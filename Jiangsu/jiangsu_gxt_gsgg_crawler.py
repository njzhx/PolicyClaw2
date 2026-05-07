
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://gxt.jiangsu.gov.cn/col/col6281/index.html"


def scrape_data():
    policies = []
    all_items = []
    url = TARGET_URL
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        

        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找包含 datastore 的 script 标签
        script_tags = soup.find_all('script')
        datastore_script = None
        
        for script in script_tags:
            if script.string and '<datastore>' in script.string:
                datastore_script = script.string
                break
        
        filtered_count = 0
        
        if datastore_script:
            # 提取 datastore 内容
            import re
            datastore_match = re.search(r'<datastore>([\s\S]*?)</datastore>', datastore_script)
            if datastore_match:
                datastore_content = datastore_match.group(1)
                
                # 提取 recordset 内容
                recordset_match = re.search(r'<recordset>([\s\S]*?)</recordset>', datastore_content)
                if recordset_match:
                    recordset_content = recordset_match.group(1)
                    
                    # 提取所有 record 内容
                    records = re.findall(r'<record><!\[CDATA\[([\s\S]*?)\]\]></record>', recordset_content)
                    
                    for record in records:
                        try:
                            # 解析 record 中的 HTML
                            record_soup = BeautifulSoup(record, 'html.parser')
                            li = record_soup.find('li')
                            if not li:
                                continue
                            
                            a_tag = li.find('a')
                            if not a_tag:
                                continue
                            
                            title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                            href = a_tag.get('href', '')
                            
                            if not title or len(title) < 5:
                                continue
                            
                            if href.startswith('/'):
                                article_url = "https://gxt.jiangsu.gov.cn" + href
                            elif not href.startswith('http'):
                                article_url = "https://gxt.jiangsu.gov.cn/col/col6281/" + href
                            else:
                                article_url = href
                            
                            pub_at = None
                            date_text = li.get_text()
                            date_match = re.search(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})', date_text)
                            if date_match:
                                try:
                                    pub_at = datetime.strptime(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}", '%Y-%m-%d').date()
                                except ValueError:
                                    pass
                            
                            # 保存到 all_items 用于显示最新5条
                            all_items.append({'title': title, 'pub_at': pub_at})
                            
                            if pub_at != yesterday:
                                filtered_count += 1
                                continue
                            
                            content = ""
                            try:
                                detail_resp = requests.get(article_url, headers=headers, timeout=15)
                                detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                                # 优先使用 #con1，然后尝试其他选择器
                                content_elem = detail_soup.select_one('#con1') or detail_soup.select_one('.content') or detail_soup.select_one('#content')
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
                                'source': '江苏省工信厅_公示公告'
                            }
                            policies.append(policy_data)
                            print(f"  Found: {title}")
                            print(f"  URL: {article_url}")
                            print(f"  Date: {pub_at}")
                            
                        except Exception:
                            continue
        
        
        print(f"✅ 江苏省工信厅_公示公告爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")
        
    except Exception as e:
        print(f"❌ 江苏省工信厅_公示公告爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省工信厅_公示公告")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        return result
    except Exception as e:
        print(f"❌ 江苏省工信厅_公示公告爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
