import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import time

SELENIUM_AVAILABLE = False
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
    print("Selenium not installed, will try alternative method")

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://sousuo.www.gov.cn/'
}

TARGET_URL = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?q=&t=zhengcelibrary&orpro="


def scrape_with_selenium():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            
            driver.set_page_load_timeout(60)
            driver.get(TARGET_URL)
            
            time.sleep(15)
            
            last_height = driver.execute_script("return document.body.scrollHeight")
            scroll_count = 0
            max_scrolls = 20
            
            while scroll_count < max_scrolls:
                for _ in range(3):
                    driver.execute_script("window.scrollBy(0, 500);")
                    time.sleep(0.5)
                
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    scroll_count += 1
                else:
                    scroll_count = 0
                    last_height = new_height
                
                if scroll_count >= 3:
                    break
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.middle_result_con'))
            )
            
            time.sleep(3)
            
            page_source = driver.page_source
            driver.quit()
            
            return page_source
        except Exception as e:
            print(f"Selenium attempt {attempt + 1} failed: {e}")
            try:
                driver.quit()
            except:
                pass
            
            if attempt < max_retries - 1:
                time.sleep(5)
    
    return None


def scrape_data():
    policies = []
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        print(f"Running date (Beijing): {today}")
        print(f"Target date: {yesterday}")
        
        page_source = None
        
        if SELENIUM_AVAILABLE:
            print("Using Selenium to render page...")
            page_source = scrape_with_selenium()
        
        if not page_source:
            print("Using direct request...")
            response = requests.get(TARGET_URL, headers=headers, timeout=30)
            response.raise_for_status()
            page_source = response.text
        
        soup = BeautifulSoup(page_source, 'html.parser')
        
        target_sections = ['国务院文件', '国务院部门文件', '国务院办公厅文件', '国务院公报', '解读、政策解读']
        all_divs = soup.find_all('div', attrs={'data-desc': True})
        
        divs = [d for d in all_divs if d.get('data-desc') in target_sections and 'middle_result_con' in d.get('class', [])]
        
        print(f"Found {len(divs)} policy list containers across sections: {target_sections}")
        
        filtered_count = 0
        
        for div in divs:
            try:
                a_tag = div.find('a', href=True)
                if not a_tag:
                    continue
                
                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                href = a_tag.get('href', '').strip()
                
                if not title or not href:
                    continue
                
                if not href.startswith('http'):
                    href = f"https://sousuo.www.gov.cn{href}"
                
                span_tags = div.find_all('span')
                date_str = ''
                for span in span_tags:
                    text = span.get_text(strip=True)
                    if re.match(r'\d{4}-\d{2}-\d{2}', text):
                        date_str = text
                        break
                
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
                    detail_resp = requests.get(href, headers=headers, timeout=15)
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
                    print(f"Detail page fetch failed: {href} - {e}")
                
                policy_data = {
                    'title': title,
                    'url': href,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '国务院文件'
                }
                
                policies.append(policy_data)
                
            except Exception as e:
                print(f"Single item processing failed - {e}")
                continue
        
        print(f"\nState Council Document Crawler: Successfully crawled {len(policies)} items from yesterday")
        print(f"Filtered out {filtered_count} non-target-date items")
        
        if all_items:
            print(f"\nAll items found on page (total: {len(all_items)}):")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items, 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else 'Unknown date'
                print(f"{i}. {item['title'][:70]} [{date_str}]")
        
    except Exception as e:
        print(f"State Council Document Crawler: Failed - {e}")
        import traceback
        traceback.print_exc()
    
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
        print("Starting Crawler: State Council Documents")
        print("----------------------------------------")
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"Crawled: {len(data)} items")
            print(f"Written to database: {len(result)} items")
            print("State Council Document Crawler: Success")
            return result, api_push_result
        else:
            print("No target date articles found")
            print("State Council Document Crawler: Completed")
            return [], None
    except Exception as e:
        print(f"State Council Document Crawler: Failed - {e}")
        return [], None


if __name__ == "__main__":
    run()
