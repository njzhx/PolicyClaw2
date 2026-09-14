# -*- coding: utf-8 -*-
"""宿迁市宿城区_政府发文 爬虫。

列表页为宿迁市区县统一信息公开模板的服务端渲染静态页，
结构为 ul.listContent > li > a[title] + span（日期），
分页 URL 为 前缀_N.shtml。共享抓取逻辑见 suqian_district_common.py。
"""

try:
    from District.suqian_district_common import (
        run_standard_crawler,
        scrape_standard,
    )
except ImportError:
    from suqian_district_common import (
        run_standard_crawler,
        scrape_standard,
    )


TARGET_URL = "http://www.sqsc.gov.cn/scq/qzfwj/xxgk_list.shtml"
SOURCE_NAME = "宿迁市宿城区_政府发文"
CATEGORY = "宿迁_宿城区"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_standard(SOURCE_NAME, TARGET_URL, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_standard_crawler(SOURCE_NAME, TARGET_URL, CATEGORY)


if __name__ == "__main__":
    run()
