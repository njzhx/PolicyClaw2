import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://doc.jiangsu.gov.cn/col/col78749/index.html',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
}

TARGET_URL = "https://doc.jiangsu.gov.cn/col/col78749/index.html"


def scrape_data():
    policies = []
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        # 发送请求获取页面内容
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        
        # 解析页面
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找文章列表
        # 注意：需要根据实际页面结构调整选择器
        article_list = soup.select('.listcon .list')
        
        # 如果没有找到文章，尝试其他选择器
        if not article_list:
            # 尝试不同的选择器
            article_list = soup.select('.article-list .article-item')
            
            if not article_list:
                article_list = soup.select('ul li')
        
        filtered_count = 0
        
        for article in article_list:
            try:
                # 提取标题和链接
                title_elem = article.select_one('a')
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                url = title_elem.get('href')
                
                if not title or len(title) < 5:
                    continue
                
                # 只处理包含"征求意见"的文章
                if "征求意见" not in title:
                    continue
                
                # 构建完整的文章 URL
                if url.startswith('/'):
                    article_url = "https://doc.jiangsu.gov.cn" + url
                elif not url.startswith('http'):
                    article_url = "https://doc.jiangsu.gov.cn" + url
                else:
                    article_url = url
                
                # 提取发布时间
                pub_at = None
                # 尝试不同的日期元素选择器
                date_elem = article.select_one('.time') or article.select_one('.date') or article.select_one('.pubtime')
                if date_elem:
                    date_str = date_elem.get_text(strip=True)
                    try:
                        # 尝试不同的日期格式
                        date_formats = ['%Y-%m-%d', '%Y年%m月%d日', '%m-%d-%Y']
                        for fmt in date_formats:
                            try:
                                pub_at = datetime.strptime(date_str, fmt).date()
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass
                
                # 如果还是没有日期，尝试从标题中提取
                if not pub_at:
                    date_pattern = r'20\d{2}[-年]\d{2}[-月]\d{2}日?'
                    date_match = re.search(date_pattern, title)
                    if date_match:
                        date_str = date_match.group(0)
                        try:
                            # 清理日期字符串
                            date_str = date_str.replace('年', '-').replace('月', '-').replace('日', '')
                            pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
                        except Exception:
                            pass
                
                # 如果还是没有日期，尝试从URL中提取
                if not pub_at:
                    url_date_pattern = r'art/(20\d{2})/(\d{1,2})/(\d{1,2})/'
                    url_date_match = re.search(url_date_pattern, article_url)
                    if url_date_match:
                        year, month, day = url_date_match.groups()
                        try:
                            pub_at = datetime(int(year), int(month), int(day)).date()
                        except Exception:
                            pass
                
                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})
                
                if pub_at != yesterday:
                    filtered_count += 1
                    continue
                
                # 获取文章内容
                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')
                    
                    # 尝试多种选择器获取内容
                    content_elem = detail_soup.select_one('div[aria-label="正文区"]')
                    
                    # 尝试常见的内容选择器
                    selectors = [
                        '.main.w1200',
                        '.main',
                        '.article-content',
                        '.content',
                        '.TRS_Editor',
                        '.article',
                        '.article-body',
                        '.main-content',
                        '#content',
                        '.text',
                        '.article_text',
                        '.art-content',
                        '.articleContent',
                        '.article-body-content',
                        '.content-main',
                        '.main-content-area'
                    ]
                    
                    if not content_elem:
                        for selector in selectors:
                            content_elem = detail_soup.select_one(selector)
                            if content_elem:
                                break
                    
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
                    'source': '江苏省商务厅'
                }
                policies.append(policy_data)
                
            except Exception:
                continue
        
        print(f"✅ 江苏省商务厅爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")
        
    except Exception as e:
        print(f"❌ 江苏省商务厅爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "江苏省商务厅_意见征集")
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
        print(f"❌ 江苏省商务厅爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
