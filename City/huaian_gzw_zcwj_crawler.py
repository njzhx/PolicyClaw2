# -*- coding: utf-8 -*-
"""淮安市国有资产监督管理委员会_政策文件 栏目爬虫。

列表页为服务端渲染 HTML（li 内日期 + 链接），分页 URL 形如 index_2.html。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.huaian_common import scrape_lb_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from huaian_common import scrape_lb_site


TARGET_URL = "https://gzw.huaian.gov.cn/col/14919_488318/index.html"
SOURCE_NAME = "淮安市国有资产监督管理委员会_政策文件"
CATEGORY = "淮安"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_lb_site(TARGET_URL, SOURCE_NAME, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )


if __name__ == "__main__":
    run()
