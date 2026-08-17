# -*- coding: utf-8 -*-
"""徐州市卫生健康委员会_政策法规 爬虫。

列表数据通过徐州统一信息公开平台 JSON 接口获取：
POST https://ws.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.xuzhou_common import scrape_gov_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from xuzhou_common import scrape_gov_site


TARGET_URL = "https://ws.xz.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003002"
SOURCE_NAME = "徐州市卫生健康委员会_政策法规"
CATEGORY = "徐州"
HOST = "https://ws.xz.gov.cn"
CATEGORY_NUM = "003002"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_gov_site(HOST, CATEGORY_NUM, SOURCE_NAME, CATEGORY)


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
