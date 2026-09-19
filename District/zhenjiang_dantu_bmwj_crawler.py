# -*- coding: utf-8 -*-
"""镇江市丹徒区_部门信息公开 爬虫。

列表页为部门信息公开导航页，页面上列各部门链接，需逐个进入
部门页面提取文件。共享抓取逻辑见 zhenjiang_district_common.py。
"""

try:
    from District.zhenjiang_district_common import (
        run_dept_crawler,
        scrape_dept_navigation,
    )
except ImportError:
    from zhenjiang_district_common import (
        run_dept_crawler,
        scrape_dept_navigation,
    )


TARGET_URL = "https://www.dantu.gov.cn/dantu/qzfbm/xxgks.shtml"
SOURCE_NAME = "镇江市丹徒区_部门信息公开"
CATEGORY = "镇江_丹徒区"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_dept_navigation(SOURCE_NAME, TARGET_URL, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_dept_crawler(SOURCE_NAME, TARGET_URL, CATEGORY)


if __name__ == "__main__":
    run()
