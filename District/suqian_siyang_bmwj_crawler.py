# -*- coding: utf-8 -*-
"""宿迁市泗阳县_部门发文 爬虫。

列表页为宿迁市区县行政规范性文件栏目（ul.gzklist > li.flex.btime），
日期从 li[stime] 属性提取，为空时从 href 路径 /YYYYMM/ 推断。
分页 URL 为 前缀_N.shtml。共享抓取逻辑见 suqian_district_common.py。
"""

try:
    from District.suqian_district_common import (
        run_gfxwj_crawler,
        scrape_gfxwj,
    )
except ImportError:
    from suqian_district_common import (
        run_gfxwj_crawler,
        scrape_gfxwj,
    )


TARGET_URL = "http://www.siyang.gov.cn/siyang/xzgfxwj/xzgfxwj_list.shtml"
SOURCE_NAME = "宿迁市泗阳县_部门发文"
CATEGORY = "宿迁_泗阳县"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_gfxwj(SOURCE_NAME, TARGET_URL, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_gfxwj_crawler(SOURCE_NAME, TARGET_URL, CATEGORY)


if __name__ == "__main__":
    run()
