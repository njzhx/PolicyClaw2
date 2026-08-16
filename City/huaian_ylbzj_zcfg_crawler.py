# -*- coding: utf-8 -*-
"""淮安市医疗保障局_政策法规 栏目爬虫。

列表数据通过政府信息公开平台 POST /articleCommonController/lists.do
JSON 接口获取，详情页为 col/art 静态页面。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.huaian_common import scrape_xxgk_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from huaian_common import scrape_xxgk_site


TARGET_URL = "https://ylbzj.huaian.gov.cn/cmsweb/zwgk/sj/indexdept.html?r=0000000064a8f16d0164ad1d9d730006&orgId=2c94939268fffddd016900c3b2a00073&orgName=%E6%B7%AE%E5%AE%89%E5%B8%82%E5%8C%BB%E7%96%97%E4%BF%9D%E9%9A%9C%E5%B1%80&topic=231"
SOURCE_NAME = "淮安市医疗保障局_政策法规"
CATEGORY = "淮安"
API_HOST = "https://ylbzj.huaian.gov.cn"
TOPIC = "231"
DEPTID = "2c94939268fffddd016900c3b2a00073"
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
