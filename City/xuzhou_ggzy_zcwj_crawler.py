# -*- coding: utf-8 -*-
"""徐州市公共资源交易中心_政策文件 爬虫。

目标栏目页（ggzy.zwb.xz.gov.cn/zcwj/zcfg.html）为纯导航页，其“政策文件”
数据指向江苏省公共资源交易平台法规接口：
GET jsggzy.jszwfw.gov.cn/EpointWebBuilder_jsggzy/zcfgInfoListAction.action
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.xuzhou_common import scrape_ggzy_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from xuzhou_common import scrape_ggzy_site


TARGET_URL = "https://ggzy.zwb.xz.gov.cn/zcwj/zcfg.html"
SOURCE_NAME = "徐州市公共资源交易中心_政策文件"
CATEGORY = "徐州"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_ggzy_site(SOURCE_NAME, CATEGORY)


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
