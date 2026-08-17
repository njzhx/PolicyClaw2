# -*- coding: utf-8 -*-
"""泰州市自然资源和规划局_政策法规 栏目爬虫。

省自然资源厅 gtapp 平台（td.nlist 列表 + POST cpage 分页）。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.taizhou_common import scrape_gtapp_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from taizhou_common import scrape_gtapp_site


TARGET_URL = "http://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=2c9082b55a69433e015a6a29489e00a9"
SOURCE_NAME = "泰州市自然资源和规划局_政策法规"
CATEGORY = "泰州"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_gtapp_site(TARGET_URL, SOURCE_NAME, CATEGORY)


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
