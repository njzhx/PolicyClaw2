# -*- coding: utf-8 -*-
"""淮安市工业和信息化局_政府信息公开 栏目爬虫。

列表数据通过政府信息公开平台 POST /articleCommonController/lists.do
JSON 接口获取，详情页为 col/art 静态页面。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.huaian_common import scrape_xxgk_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from huaian_common import scrape_xxgk_site


TARGET_URL = "https://gxj.huaian.gov.cn/cmsweb/zwgk/sj/indexdept.html?r=0000000064a8f16d0164ad1d9d730006&orgId=5e38cfb8632dc46d01633835ec240146&orgName=%E6%B7%AE%E5%AE%89%E5%B8%82%E5%B7%A5%E4%B8%9A%E5%92%8C%E4%BF%A1%E6%81%AF%E5%8C%96%E5%B1%80"
SOURCE_NAME = "淮安市工业和信息化局_政府信息公开"
CATEGORY = "淮安"
API_HOST = "https://gxj.huaian.gov.cn"
TOPIC = ""
DEPTID = "5e38cfb8632dc46d01633835ec240146"
RDEPTID = ""


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_xxgk_site(
        API_HOST,
        SOURCE_NAME,
        CATEGORY,
        topic=TOPIC,
        deptid=DEPTID,
        rdeptid=RDEPTID,
    )


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
