# -*- coding: utf-8 -*-
"""镇江市发展和改革委员会_通知公示 爬虫。

列表页为镇江市政府统一信息公开模板的服务端渲染静态页，
结构为 li > a[title] + span.time，分页 URL 为 前缀_N.shtml。
共享抓取逻辑见 zhenjiang_common.py。
"""

try:
    from City.zhenjiang_common import run_crawler, scrape_channels
except ImportError:  # pragma: no cover - 兼容直接运行
    from zhenjiang_common import run_crawler, scrape_channels


TARGET_URL = "https://fgw.zhenjiang.gov.cn/fgw/tzgg/list.shtml"
SOURCE_NAME = "镇江市发展和改革委员会_通知公示"
CATEGORY = "镇江"

CHANNEL_URLS = [
    "https://fgw.zhenjiang.gov.cn/fgw/tzgg/list.shtml",
]


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_channels(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_crawler(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


if __name__ == "__main__":
    run()
