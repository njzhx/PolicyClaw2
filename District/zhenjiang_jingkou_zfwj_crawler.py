# -*- coding: utf-8 -*-
"""镇江市京口区_政府发文 爬虫。

列表页为镇江市区县统一信息公开模板的服务端渲染静态页，
结构为 li > a[title] + span.time，分页 URL 为 前缀_N.shtml。
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


TARGET_URL = "https://www.jingkou.gov.cn/jingkou/zfwj/xxgkpt_list.shtml"
SOURCE_NAME = "镇江市京口区_政府发文"
CATEGORY = "镇江_京口区"

CHANNEL_URLS = [
    "https://www.jingkou.gov.cn/jingkou/zfwj/xxgkpt_list.shtml",
]


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_channels(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_channel_crawler(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


if __name__ == "__main__":
    run()
