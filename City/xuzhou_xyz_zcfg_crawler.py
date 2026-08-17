# -*- coding: utf-8 -*-
"""信用徐州_政策法规 爬虫。

列表数据通过 GET www.xuzhoucredit.gov.cn/wcm/content/news_list.json 获取，
“政策法规”父栏目下聚合国家法规、省内法规、政策研究、政策制度、
标准规范全部子栏目。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.xuzhou_common import scrape_credit_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from xuzhou_common import scrape_credit_site


TARGET_URL = "https://www.xuzhoucredit.gov.cn/wcm/column/zcgf.html"
SOURCE_NAME = "信用徐州_政策法规"
CATEGORY = "徐州"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_credit_site(SOURCE_NAME, CATEGORY)


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
