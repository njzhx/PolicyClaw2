# -*- coding: utf-8 -*-
"""国家税务总局泰州市税务局_通知公告 栏目爬虫。

省税务局 chinatax 平台（dataproxy.jsp XML 分页）。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.taizhou_common import scrape_chinatax_column
except ImportError:  # pragma: no cover - 兼容直接运行
    from taizhou_common import scrape_chinatax_column


TARGET_URL = "https://jiangsu.chinatax.gov.cn/col/col9386/index.html"
SOURCE_NAME = "国家税务总局泰州市税务局_通知公告"
CATEGORY = "泰州"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_chinatax_column(TARGET_URL, SOURCE_NAME, CATEGORY)


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
