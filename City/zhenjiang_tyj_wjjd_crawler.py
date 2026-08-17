# -*- coding: utf-8 -*-
"""镇江市体育局_文件解读 爬虫。

"文件/解读"栏目为聚合页，本身无静态列表，实际数据来自
"部门文件"与"政策解读"两个子栏目的 xxgk_list.shtml 静态分页，
本爬虫一次性抓取并汇总两个子栏目。
共享抓取逻辑见 zhenjiang_common.py。
"""

try:
    from City.zhenjiang_common import run_crawler, scrape_channels
except ImportError:  # pragma: no cover - 兼容直接运行
    from zhenjiang_common import run_crawler, scrape_channels


TARGET_URL = "http://tyj.zhenjiang.gov.cn/tyj/xxgkwjjd/xxgk_lists.shtml"
SOURCE_NAME = "镇江市体育局_文件解读"
CATEGORY = "镇江"

CHANNEL_URLS = [
    "http://tyj.zhenjiang.gov.cn/tyj/xxgkbmwj/xxgk_list.shtml",
    "http://tyj.zhenjiang.gov.cn/tyj/xxgkzcjd/xxgk_list.shtml",
]


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_channels(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
    return run_crawler(SOURCE_NAME, CHANNEL_URLS, CATEGORY)


if __name__ == "__main__":
    run()
