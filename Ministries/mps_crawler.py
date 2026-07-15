import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re
import asyncio

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

TARGET_URL = "https://www.mps.gov.cn/n6557558/index.html"
BASE_URL = "https://www.mps.gov.cn"

CRAWL4AI_AVAILABLE = False
try:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    CRAWL4AI_AVAILABLE = True
except ImportError:
    print("[WARN] crawl4ai not installed")

SELENIUM_AVAILABLE = False
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    print("[WARN] Selenium not installed")


def parse_date(date_str):
    patterns = [
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})/(\d{2})/(\d{2})',
        r'(\d{4})年(\d{2})月(\d{2})日',
    ]
    for pattern in patterns:
        match = re.search(pattern, date_str)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
            except ValueError:
                continue
    return None


async def scrape_with_crawl4ai():
    if not CRAWL4AI_AVAILABLE:
        return None

    config = CrawlerRunConfig(
        page_timeout=60000,
        remove_overlay_elements=True,
        wait_for="css:li",
        screenshot=False,
    )

    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(TARGET_URL, config=config)

            if result.success:
                return result.markdown
            else:
                print(f"[WARN] crawl4ai failed: {result.error_message}")
                return None
    except Exception as e:
        print(f"[WARN] crawl4ai error: {e}")
        return None


def scrape_with_selenium():
    if not SELENIUM_AVAILABLE:
        return None

    import time
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_argument('--disable-blink-features=AutomationControlled')

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(45)
        driver.get(TARGET_URL)
        time.sleep(10)

        page_source = driver.page_source
        driver.quit()
        return page_source
    except Exception as e:
        print(f"[WARN] Selenium error: {e}")
        try:
            driver.quit()
        except:
            pass
        return None


def get_article_content(url):
    content = ""
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        content_div = soup.find('div', class_='wordContent w915')
        if content_div:
            content = content_div.get_text(separator='\n', strip=True)
        else:
            content_div = soup.find('div', class_='TRS_Editor') or soup.find('div', class_='content')
            if content_div:
                content = content_div.get_text(separator='\n', strip=True)

    except Exception as e:
        print(f"[WARN] 抓取详情页失败: {e}")
    return content


async def scrape_data_async():
    policies = []
    all_items = []

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"[INFO] 运行日期（北京时间）：{today}")
        print(f"[INFO] 目标抓取日期：{target_date_label}")

        page_source = None
        markdown_content = None

        if CRAWL4AI_AVAILABLE:
            print("[INFO] 使用crawl4ai获取页面...")
            try:
                markdown_content = await scrape_with_crawl4ai()
            except Exception as e:
                print(f"[WARN] crawl4ai执行失败: {e}")
                markdown_content = None

        if not markdown_content and SELENIUM_AVAILABLE:
            print("[INFO] 使用Selenium获取页面...")
            try:
                page_source = scrape_with_selenium()
            except Exception as e:
                print(f"[WARN] Selenium执行失败: {e}")
                page_source = None

        if not markdown_content and not page_source:
            print("[INFO] 使用requests获取页面...")
            try:
                response = requests.get(TARGET_URL, headers=headers, timeout=30)
                response.raise_for_status()
                page_source = response.text
            except Exception as e:
                print(f"[WARN] requests执行失败: {e}")
                page_source = None

        if markdown_content:
            print("[INFO] 从markdown中提取文章数据...")
            article_pattern = r'\*\s*(\d{4}-\d{2}-\d{2})\s*\[(.+?)\]\((https?://[^)]+)\)'
            articles = re.findall(article_pattern, markdown_content)

            print(f"[INFO] 找到 {len(articles)} 条数据")

            for date_str, title, href in articles:
                try:
                    pub_at = parse_date(date_str)
                    all_items.append({'title': title, 'pub_at': pub_at, 'url': href})
                except Exception as e:
                    print(f"[WARN] 解析失败: {e}")
                    continue
        elif page_source:
            soup = BeautifulSoup(page_source, 'html.parser')

            ul_list = soup.find('ul', class_='list')
            if ul_list:
                lis = ul_list.find_all('li')
                print(f"[INFO] 找到 {len(lis)} 条数据")

                for li in lis:
                    try:
                        a = li.find('a', href=True)
                        if not a:
                            continue

                        title = a.get_text(strip=True)
                        href = a.get('href', '')

                        if not title or not href:
                            continue

                        if not href.startswith('http'):
                            article_url = BASE_URL + href
                        else:
                            article_url = href

                        date_spans = li.find_all('span')
                        date_str = ''
                        for span in date_spans:
                            text = span.get_text(strip=True)
                            if re.match(r'\d{4}-\d{2}-\d{2}', text):
                                date_str = text
                                break

                        pub_at = parse_date(date_str)
                        all_items.append({'title': title, 'pub_at': pub_at, 'url': article_url})

                    except Exception as e:
                        print(f"[WARN] 单条数据处理失败: {e}")
                        continue

        filtered_count = 0

        for item in all_items:
            try:
                pub_at = item['pub_at']

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = get_article_content(item['url'])

                policy_data = {
                    'title': item['title'],
                    'url': item['url'],
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '中央部委',
                    'source': '公安部政策文件'
                }

                policies.append(policy_data)

            except Exception as e:
                print(f"[WARN] 单条数据处理失败: {e}")
                continue

        print(f"\n[OK] 公安部政策文件爬虫：成功抓取 {len(policies)} 条目标日期窗口数据")
        print(f"[SKIP] 过滤掉 {filtered_count} 条非目标日期的数据")

        if all_items:
            print(f"\n[INFO] 页面最新5条是：")
            sorted_items = sorted(all_items, key=lambda x: x['pub_at'] or datetime.min.date(), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                title = item['title'][:50]
                print(f"[OK] {title}... {date_str}")

    except Exception as e:
        print(f"[ERROR] 公安部政策文件爬虫：抓取失败 - {e}")
        print("----------------------------------------")

    return policies, all_items


def scrape_data():
    return asyncio.run(scrape_data_async())


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "公安部政策文件")
    except Exception as e:
        print(f"Error saving to database: {e}")
        return data_list, None


def run():
    try:
        data, _ = scrape_data()
        if data:
            result, api_push_result = save_to_supabase(data)
            print(f"\n[OK] 写入数据库: {len(result)} 条")
            print("----------------------------------------")
            print("[OK] 爬虫 公安部政策文件 执行成功")
            return result, api_push_result
        else:
            print(f"\n[OK] 写入数据库: 0 条")
            print("----------------------------------------")
            print("[WARN] 未找到目标日期的文章")
            return [], None
    except Exception as e:
        print(f"[ERROR] 爬虫 公安部政策文件 运行失败 - {e}")
        print("----------------------------------------")
        return [], None


if __name__ == "__main__":
    run()
