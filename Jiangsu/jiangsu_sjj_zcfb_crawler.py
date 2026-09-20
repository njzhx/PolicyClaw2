SOURCE_NAME = '江苏省数据局_政策发布'


import requests
from io import BytesIO
from bs4 import BeautifulSoup
from pypdf import PdfReader
from urllib.parse import urljoin
from datetime import datetime, timedelta, timezone

from crawler_core import CrawlerMetrics, CrawlerRunResult, extract_content_text, get_crawl_date_window, is_target_date, parse_date
from db_utils import save_to_policy
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

TARGET_URL = "https://jszwb.jiangsu.gov.cn/col/col81698/index.html?number=A00003"


def scrape_data():
    policies = []
    all_items = []
    metrics = CrawlerMetrics()
    url = TARGET_URL

    try:
        target_date_from, target_date_to = get_crawl_date_window()
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        items = []
        data_store = soup.find('div', id='395700')
        if data_store:
            script_tag = data_store.find('script', type='text/xml')
            if script_tag:
                datastore_soup = BeautifulSoup(script_tag.string, 'html.parser')
                records = datastore_soup.find_all('record')
                for record in records:
                    cdata = record.string
                    if cdata:
                        record_soup = BeautifulSoup(cdata, 'html.parser')
                        li_elems = record_soup.find_all('li')
                        items.extend(li_elems)

        if not items:
            items = soup.find_all('li')
        metrics.raw_item_count = len(items)
        filtered_count = 0

        for item in items:
            try:
                a_tag = item.find('a')
                if not a_tag:
                    continue

                title = a_tag.get('title', '').strip() or a_tag.get_text(strip=True)
                href = a_tag.get('href', '')

                if not title or len(title) < 5:
                    continue

                if href.startswith('/'):
                    article_url = "https://jszwb.jiangsu.gov.cn" + href
                elif not href.startswith('http'):
                    article_url = "https://jszwb.jiangsu.gov.cn/col/col81698/" + href
                else:
                    article_url = href

                pub_at = None
                date_text = item.get_text()
                date_match = re.search(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})', date_text)
                if date_match:
                    try:
                        pub_at = parse_date(f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}")
                    except ValueError:
                        pass

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue
                metrics.valid_item_count += 1
                all_items.append({'title': title, 'pub_at': pub_at})

                if not is_target_date(pub_at, target_date_from, target_date_to):
                    filtered_count += 1
                    continue

                content = ""
                try:
                    detail_resp = requests.get(article_url, headers=headers, timeout=15)
                    detail_soup = BeautifulSoup(detail_resp.content, 'html.parser')

                    # 使用指定的CSS类查找内容区域
                    # 类名：main-txt bfr_article_content default-defaultMode normalFontSize
                    content_elem = detail_soup.select_one('.main-txt.bfr_article_content.default-defaultMode.normalFontSize')

                    # 如果找不到特定的内容区域，尝试其他选择器
                    if not content_elem:
                        content_elem = detail_soup.select_one('.content') or detail_soup.select_one('#content')

                    # 如果还是找不到，尝试查找包含大量文本的div
                    if not content_elem:
                        divs = detail_soup.find_all('div')
                        for div in divs:
                            text = div.get_text(strip=True)
                            if text and len(text) > 500:
                                content_elem = div
                                break

                    if content_elem:
                        content = extract_content_text(content_elem)
                    if not content:
                        attachment = detail_soup.select_one('a[href$=".pdf"], a[href*="downfile.jsp"]')
                        if attachment:
                            pdf_url = urljoin(article_url, attachment.get('href', ''))
                            try:
                                pdf_resp = requests.get(pdf_url, headers=headers, timeout=30)
                                pdf_resp.raise_for_status()
                                content = '\n'.join(
                                    (page.extract_text() or '').strip()
                                    for page in PdfReader(BytesIO(pdf_resp.content)).pages
                                ).strip()
                            except Exception as exc:
                                metrics.errors.append(f"PDF附件正文提取失败: {pdf_url} - {exc}")
                            if not content:
                                metrics.errors.append(f"[ATTACHMENT_ONLY] PDF附件无可提取文本: {article_url}")
                        else:
                            metrics.errors.append(f"[CONTENT_MISSING] 正文为空: {article_url}")
                except Exception as exc:
                    metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")

                policy_data = {
                    'title': title,
                    'url': article_url,
                    'pub_at': pub_at,
                    'content': content,
                    'selected': False,
                    'category': '江苏省本级',
                    'source': SOURCE_NAME
                }
                policies.append(policy_data)

            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {exc}")

    except Exception as e:
        metrics.errors.append(f"列表页抓取失败: {TARGET_URL} - {e}")

    metrics.filtered_count = filtered_count if 'filtered_count' in locals() else 0
    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(not item.get('content') for item in policies)
    return policies, all_items[:5], metrics


def run():
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(items=processed_items, latest_items=latest_items,
                            metrics=metrics, api_push_result=api_push_result)


if __name__ == "__main__":
    run()
