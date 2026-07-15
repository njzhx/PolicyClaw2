import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from crawler_core import format_date_window, get_crawl_date_window, is_target_date
import re
import io

# 目标网站URL
TARGET_URL = "https://kxjst.jiangsu.gov.cn/col/col82540/index.html"
SOURCE_NAME = "江苏省科学技术厅_通知公告"

# 请求头
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

def extract_pdf_text(pdf_url):
    """从PDF链接提取文字内容（支持pdfplumber和pypdf双引擎）"""
    pdf_text = ""

    # 先尝试请求PDF内容
    try:
        pdf_resp = requests.get(pdf_url, headers=headers, timeout=60)
        if len(pdf_resp.content) < 100:
            return ""
    except Exception as e:
        print(f"PDF download failed: {str(e)[:30]}")
        return ""

    # 引擎1: pdfplumber (优先)
    try:
        import pdfplumber
        with io.BytesIO(pdf_resp.content) as data:
            with pdfplumber.open(data) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    pdf_text += page_text + "\n"
                if len(pdf_text.strip()) > 50:
                    print(f"[PDF] pdfplumber: {len(pdf_text.strip())} chars")
                    return pdf_text.strip()
    except ImportError:
        print("[PDF] pdfplumber not installed, try pypdf...")
    except Exception as e:
        print(f"[PDF] pdfplumber failed: {type(e).__name__}: {str(e)[:30]}")

    # 引擎2: pypdf (备选)
    try:
        from pypdf import PdfReader
        pdf_text2 = ""
        with io.BytesIO(pdf_resp.content) as data:
            reader = PdfReader(data)
            for page in reader.pages:
                pdf_text2 += page.extract_text() or ""
            if len(pdf_text2.strip()) > 50:
                print(f"[PDF] pypdf: {len(pdf_text2.strip())} chars")
                return pdf_text2.strip()
    except ImportError:
        print("[PDF] pypdf also not installed!")
    except Exception as e:
        print(f"[PDF] pypdf failed: {type(e).__name__}: {str(e)[:30]}")

    return pdf_text.strip() if len(pdf_text.strip()) > 50 else ""

# ==========================================
# 1. 网页抓取逻辑
# ==========================================
def scrape_data():
    """抓取数据，返回与表结构一致的字典列表"""
    policies = []
    all_items_list = []

    try:
        # 计算目标日期窗口日期（使用北京时间 UTC+8）
        target_date_from, target_date_to = get_crawl_date_window()
        target_date_label = format_date_window(target_date_from, target_date_to)
        today = datetime.now(timezone(timedelta(hours=8))).date()

        print(f"📅 运行日期（北京时间）：{today}")
        print(f"🎯 目标抓取日期：{target_date_label}")

        # 请求页面
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        # 查找列表容器
        ul_list = soup.find_all('ul', class_='column-list')
        all_records = []

        for ul in ul_list:
            lis = ul.find_all('li', class_='cf')
            for li in lis:
                all_records.append(li)

        print(f"📋 找到 {len(all_records)} 条记录")

        filtered_count = 0

        for li in all_records:
            a = li.find('a')
            span = li.find('span')

            if not a:
                continue

            title = a.get('title', '') or a.get_text(strip=True)
            href = a.get('href', '')
            date_str = span.get_text(strip=True) if span else ''

            if not title or not href:
                continue

            # 保存到all_items_list用于预览
            all_items_list.append({'title': title, 'date_str': date_str})

            # 解析日期
            try:
                pub_at = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                filtered_count += 1
                continue

            # 只保留目标日期的
            if not is_target_date(pub_at, target_date_from, target_date_to):
                filtered_count += 1
                continue

            # 处理URL
            if not href.startswith('http'):
                href = f"https://kxjst.jiangsu.gov.cn{href}" if href.startswith('/') else f"https://kxjst.jiangsu.gov.cn/{href}"

            # 抓取正文（p标签内容 + PDF附件文字）
            content = ""
            try:
                resp = requests.get(href, headers=headers, timeout=30)
                resp.raise_for_status()
                resp.encoding = 'utf-8'
                ds = BeautifulSoup(resp.text, 'html.parser')

                # 1. 先提取p标签中的文字
                p_content = []
                p_tags = ds.find_all('p')
                for p in p_tags:
                    text = p.get_text(strip=True)
                    if text and len(text) > 5 and '点击正文' not in text:
                        p_content.append(text)
                content = "\n".join(p_content)
                if content:
                    print(f"[p-tag] {len(content)} chars")

                # 2. 查找PDF附件并提取文字（优先）
                all_links = ds.find_all('a', href=True)
                for a_link in all_links:
                    link_href = a_link.get('href', '')
                    link_text = a_link.get_text(strip=True)

                    is_pdf_link = ('downfile' in link_href.lower() or
                                  '.pdf' in link_href.lower() or
                                  '下载' in link_text or
                                  '附件' in link_text or
                                  '点击正文' in link_text)

                    if is_pdf_link:
                        # 构建PDF完整URL
                        if not link_href.startswith('http'):
                            if link_href.startswith('/'):
                                pdf_url = f"https://kxjst.jiangsu.gov.cn{link_href}"
                            else:
                                pdf_url = f"https://kxjst.jiangsu.gov.cn/{link_href}"
                        else:
                            pdf_url = link_href

                        # 提取PDF文字
                        pdf_text = extract_pdf_text(pdf_url)
                        if pdf_text and len(pdf_text) > len(content):
                            content = pdf_text
                            break

                print(f"[CONTENT] Final: {len(content)} chars")

            except Exception as e:
                print(f"Detail fetch failed: {title[:20]} | {str(e)[:40]}")

            policy_data = {
                'title': title,
                'url': href,
                'pub_at': pub_at,
                'content': content,
                'selected': False,
                'category': '江苏省本级',
                'source': SOURCE_NAME
            }
            policies.append(policy_data)

        print(f"✅ 成功抓取目标日期窗口数据：{len(policies)} 条")
        print(f"⏭️  过滤非目标日期窗口/无效数据：{filtered_count} 条")

        # 打印最新5条预览
        if all_items_list:
            print(f"\n📊 页面最新5条是：")
            sorted_items = sorted(all_items_list, key=lambda x: x.get('date_str', ''), reverse=True)
            for i, item in enumerate(sorted_items[:5], 1):
                d = item.get('date_str', '')
                t = item.get('title', '')[:50]
                print(f"✅ [{d}] {t}")

    except Exception as e:
        print(f"❌ 抓取失败：{e}")

    return policies, len(all_items_list)

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
            print(f"\n💾 写入数据库：{len(result)} 条")
            print("----------------------------------------")
            print("✅ 爬虫 江苏省科技厅_通知公告 执行成功")
            return result, api_push_result
        else:
            print(f"\n💾 写入数据库：0 条")
            print("----------------------------------------")
            print("⚠️  未找到目标日期窗口发布的通知公告")
            return [], None
    except Exception as e:
        print(f"❌ 爬虫运行失败：{e}")
        print("----------------------------------------")
        return [], None

# ==========================================
# 主入口
# ==========================================
if __name__ == "__main__":
    run()
