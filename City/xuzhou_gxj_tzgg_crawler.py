# -*- coding: utf-8 -*-
"""徐州市工业和信息化局_通知公告 爬虫。

列表数据通过徐州统一信息公开平台 JSON 接口获取：
POST https://gxj.xz.gov.cn/EWB-FRONT/rest/lightfrontaction/getgovinfolist
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.xuzhou_common import scrape_gov_site
except ImportError:
    from xuzhou_common import scrape_gov_site


TARGET_URL = "https://gxj.xz.gov.cn/dynamic/zwgk/govInfoPub.html?categorynum=003010"
SOURCE_NAME = "徐州市工业和信息化局_通知公告"
CATEGORY = "徐州"
HOST = "https://gxj.xz.gov.cn"
CATEGORY_NUM = "003010"


def scrape_data():
    return scrape_gov_site(HOST, CATEGORY_NUM, SOURCE_NAME, CATEGORY)


def run():
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