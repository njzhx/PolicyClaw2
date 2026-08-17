# -*- coding: utf-8 -*-
"""镇江市人民政府_政策解读 爬虫。

列表页为镇江市政府统一信息公开模板的服务端渲染静态页，
结构为 li > a[title] + span.time，分页 URL 为 前缀_N.shtml。
共享抓取逻辑见 zhenjiang_common.py。
"""

try:
    from City.zhenjiang_common import run_crawler, scrape_channels
except ImportError:  # pragma: no cover - 兼容直接运行
    from zhenjiang_common import run_crawler, scrape_channels


TARGET_URL = "https://www.zhenjiang.gov.cn/zhenjiang/zcjd/xxgk_list.shtml"
SOURCE_NAME = "镇江市人民政府_政策解读"
CATEGORY = "镇江"

CHANNEL_URLS = [
    "https://www.zhenjiang.gov.cn/zhenjiang/zcjd/xxgk_list.shtml",
]


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_channels(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_crawler(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


if __name__ == "__main__":
    run()
