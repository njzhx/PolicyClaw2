# -*- coding: utf-8 -*-
"""镇江市句容市_政府发文 爬虫。

列表页为句容市政府文件导航页（jrxxgkpt_navs.shtml），页面上有
行政规范性文件、行政规范性关联文件、市政府文件三个子栏目，
本爬虫聚合抓取各子栏目（市政府办文件子栏目有单独爬虫）。
共享抓取逻辑见 zhenjiang_district_common.py。
"""

try:
    from District.zhenjiang_district_common import (
        run_channel_crawler,
        scrape_channels,
    )
except ImportError:
    from zhenjiang_district_common import (
        run_channel_crawler,
        scrape_channels,
    )


TARGET_URL = "https://www.jurong.gov.cn/jurong/c100276/jrxxgkpt_navs.shtml"
SOURCE_NAME = "镇江市句容市_政府发文"
CATEGORY = "镇江_句容市"

CHANNEL_URLS = [
    "https://www.jurong.gov.cn/jurong/c100277/jrxxgkpt_zfgzk.shtml",
    "https://www.jurong.gov.cn/jurong/xzgfxglwj/jrxxgkpt_list.shtml",
    "https://www.jurong.gov.cn/jurong/c100278/jrxxgkpt_list.shtml",
]


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_channels(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_channel_crawler(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


if __name__ == "__main__":
    run()
