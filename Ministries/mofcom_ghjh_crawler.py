import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://www.mofcom.gov.cn/ghjh/index.html',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
}

TARGET_URL = "https://www.mofcom.gov.cn/ghjh/index.html"


def get_article_list():
    """获取文章列表"""
    try:
        # 从页面中提取必要的参数
        page_url = TARGET_URL
        response = requests.get(page_url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找script标签获取参数
        script_tag = soup.select_one('script[parsetype="bulidstatic"]')
        if not script_tag:
            print("❌ 未找到加载文章列表的script标签")
            return []
        
        # 提取querydata参数
        querydata_str = script_tag.get('querydata')
        if not querydata_str:
            print("❌ 未找到querydata参数")
            return []
        
        # 提取url参数
        api_url = script_tag.get('url')
        if not api_url:
            print("❌ 未找到api_url参数")
            return []
        
        # 构建完整的API URL
        if api_url.startswith('/'):
            api_url = "https://www.mofcom.gov.cn" + api_url
        
        # 解析querydata
        try:
            # 替换单引号为双引号以符合JSON格式
            querydata_str = querydata_str.replace("'", "\"")
            querydata = json.loads(querydata_str)
        except Exception as e:
            print(f"❌ 解析querydata失败: {e}")
            # 手动构建querydata
            querydata = {
                'parseType': 'bulidstatic',
                'webId': '8f43c7ad3afc411fb56f281724b73708',
                'tplSetId': '52551ea0e2c14bca8c84792f7aa37ead',
                'pageType': 'column',
                'tagId': '分页列表',
                'editType': 'null',
                'pageId': 'fc8bdff48fa345a48b651c1285b70b8f'
            }
        
        # 准备API请求参数
        api_headers = {
            'User-Agent': headers['User-Agent'],
            'Referer': page_url
        }
        
        # 发送API请求（使用GET方法）
        try:
            api_response = requests.get(api_url, params=querydata, headers=api_headers, timeout=30)
            api_response.raise_for_status()
            
            # 解析API响应
            data = api_response.json()
            if data.get('code') != '200' and data.get('code') != 200:
                print(f"❌ API请求失败: {data.get('message') or data.get('msg')}")
                return []
            
            # 提取文章列表HTML
            html_content = data.get('data', {}).get('html', '')
            if not html_content:
                print("❌ API响应中没有HTML内容")
                return []
            
            # 解析HTML获取文章列表
            article_soup = BeautifulSoup(html_content, 'html.parser')
            article_list = article_soup.find_all('li')
            
            articles = []
            for article in article_list:
                # 提取标题和链接
                title_elem = article.find('a')
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                url = title_elem.get('href')
                
                if not title or len(title) < 5:
                    continue
                
                # 构建完整的文章 URL
                if url.startswith('/'):
                    article_url = "https://www.mofcom.gov.cn" + url
                elif not url.startswith('http'):
                    article_url = "https://www.mofcom.gov.cn" + url
                else:
                    article_url = url
                
                # 提取发布时间
                pub_at = None
                # 查找所有span元素，日期在span中但没有class
                date_elem = article.find('span')
                if date_elem:
                    date_str = date_elem.get_text(strip=True)
                    # 清理日期字符串，去掉方括号
                    date_str = date_str.replace('[', '').replace(']', '')
                    try:
                        pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
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
                
                articles.append({
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at
                })
            
            return articles
            
        except Exception as e:
            print(f"❌ 获取文章列表失败: {e}")
            return []
        
    except Exception as e:
        print(f"❌ 获取文章列表失败: {e}")
        return []


def get_article_content(url):
    """获取文章内容"""
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 优先从指定的div容器中提取内容
        content_elem = soup.select_one('div[ergodic="article"].art-con.art-con-bottonmLine[aria-region="true"][aria-autolabel="true"][aria-label="正文区"]')
        
        # 尝试找到所有.art-con.art-con-bottonmLine容器并选择内容最长的那个
        if not content_elem:
            art_con_containers = soup.select('.art-con.art-con-bottonmLine')
            if art_con_containers:
                # 选择内容最长的容器
                content_elem = max(art_con_containers, key=lambda x: len(x.get_text(strip=True)))
        
        # 尝试常见的内容选择器
        selectors = [
            '.article-content',
            '.art-con',
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
                content_elem = soup.select_one(selector)
                if content_elem:
                    break
        
        if content_elem:
            return content_elem.get_text(strip=True)
        else:
            return ""
    except Exception:
        return ""


def scrape_data():
    policies = []
    all_items = []
    
    try:
        tz_utc8 = timezone(timedelta(hours=8))
        today = datetime.now(tz_utc8).date()
        yesterday = today - timedelta(days=1)
        
        # 获取文章列表
        articles = get_article_list()
        print(f"📋 找到 {len(articles)} 篇文章")
        
        # 过滤出有日期的文章
        articles_with_date = [a for a in articles if a['pub_at']]
        print(f"📅 有日期的文章: {len(articles_with_date)} 篇")
        
        filtered_count = 0
        
        for article in articles_with_date:
            try:
                title = article['title']
                article_url = article['url']
                pub_at = article['pub_at']
                
                # 保存到 all_items 用于显示最新5条
                all_items.append({'title': title, 'pub_at': pub_at})
                
                if pub_at != yesterday:
                    filtered_count += 1
                    continue
                
                # 获取文章内容
                content = get_article_content(article_url)
                
                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '',
                    'source': '商务部规划计划'
                }
                policies.append(policy_data)
                
            except Exception:
                continue
        
        print(f"✅ 商务部规划计划爬虫：成功抓取 {len(policies)} 条前一天数据")
        print(f"⏭️  过滤掉 {filtered_count} 条非目标日期的数据")
        
        # 显示页面最新5条
        if all_items:
            print("📊 页面最新5条是：")
            for i, item in enumerate(all_items[:5], 1):
                date_str = item['pub_at'].strftime('%Y-%m-%d') if item['pub_at'] else '未知日期'
                print(f"✅ {item['title']} {date_str}")
        
    except Exception as e:
        print(f"❌ 商务部规划计划爬虫：抓取失败 - {e}")
        print("----------------------------------------")
    
    return policies, all_items


def save_to_supabase(data_list):
    try:
        from db_utils import save_to_policy
        return save_to_policy(data_list, "商务部_规划计划")
    except Exception:
        return data_list


def run():
    try:
        data, _ = scrape_data()
        result = save_to_supabase(data)
        print(f"💾 写入数据库: {len(data)} 条")
        print("----------------------------------------")
        
        # 测试内容抓取
        if data:
            print("📄 测试内容抓取：")
            print(f"标题: {data[0]['title']}")
            print(f"链接: {data[0]['url']}")
            print(f"内容长度: {len(data[0]['content'])} 字符")
            print(f"内容预览: {data[0]['content'][:500]}...")
        
        return result
    except Exception as e:
        print(f"❌ 商务部规划计划爬虫：运行失败 - {e}")
        print("----------------------------------------")
        return []


if __name__ == "__main__":
    run()
