# -*- coding: utf-8 -*-
"""泰州市靖江市_部门发文 爬虫。

列表页基于泰州 jpaas 集约化平台，通过 jpaas-publish-server
API 动态分页返回 HTML 片段。共享抓取逻辑见 City/taizhou_common.py。
"""

try:
    from City.taizhou_common import scrape_taizhou_column
except ImportError:
    from taizhou_common import scrape_taizhou_column

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy


TARGET_URL = "https://www.jingjiang.gov.cn/xxgk/fdzdgknr/fggw/bmwj/index.html"
SOURCE_NAME = "泰州市靖江市_部门发文"
CATEGORY = "泰州_靖江市"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_taizhou_column(TARGET_URL, SOURCE_NAME, CATEGORY)


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
