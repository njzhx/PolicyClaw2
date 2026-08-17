# -*- coding: utf-8 -*-
"""徐州市自然资源和规划局_政策文件 爬虫。

列表页为江苏自然资源网站的信息公开栏目（服务端渲染 td.nlist 结构），
第 1 页 GET，后续页 POST cpage 参数翻页。
"""

from crawler_core import CrawlerRunResult
from db_utils import save_to_policy

try:
    from City.xuzhou_common import scrape_zrzy_site
except ImportError:  # pragma: no cover - 兼容直接运行
    from xuzhou_common import scrape_zrzy_site


TARGET_URL = "https://zrzy.jiangsu.gov.cn/gtapp/nrglIndex.action?classID=2c9082b55a69433e015a6a201c54007f"
SOURCE_NAME = "徐州市自然资源和规划局_政策文件"
CATEGORY = "徐州"


def scrape_data():
    """返回 (policies, latest_items, metrics)。"""
    return scrape_zrzy_site(TARGET_URL, SOURCE_NAME, CATEGORY)


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
