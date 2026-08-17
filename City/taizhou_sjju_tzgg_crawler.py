# -*- coding: utf-8 -*-
"""泰州市数据局_通知公告 栏目爬虫。

列表页为 jpaas 动态加载（AuthorizedRead + build/unit JSON 接口分页）。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.taizhou_common import scrape_taizhou_column
except ImportError:  # pragma: no cover - 兼容直接运行
    from taizhou_common import scrape_taizhou_column


TARGET_URL = "https://zwfw.taizhou.gov.cn/xwzx/tztg/index.html"
SOURCE_NAME = "泰州市数据局_通知公告"
CATEGORY = "泰州"


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
