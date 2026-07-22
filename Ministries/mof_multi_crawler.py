from urllib.parse import urljoin
from types import SimpleNamespace

import requests
from bs4 import BeautifulSoup

from crawler_core import (
    CrawlerMetrics,
    CrawlerRunResult,
    get_crawl_date_window,
    is_target_date,
    parse_date,
)
from db_utils import save_to_policy


TARGET_URL = "https://www.mof.gov.cn/"
CATEGORY = "中央部委"
MAX_PAGES = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

CRAWLER_CONFIGS = [
    {
        "name": "财政部经济建设司_通知公告",
        "url": "https://jjs.mof.gov.cn/tongzhigonggao/",
        "source": "财政部经济建设司_通知公告",
    },
    {
        "name": "财政部经济建设司_政策法规",
        "url": "https://jjs.mof.gov.cn/zhengcefagui/",
        "source": "财政部经济建设司_政策法规",
    },
    {
        "name": "财政部农业农村司_政策发布",
        "url": "https://nys.mof.gov.cn/czpjZhengCeFaBu_2_2/",
        "source": "财政部农业农村司_政策发布",
    },
    {
        "name": "财政部社会保障司_工作动态",
        "url": "https://sbs.mof.gov.cn/gongzuodongtai/",
        "source": "财政部社会保障司_工作动态",
    },
    {
        "name": "财政部社会保障司_政策发布",
        "url": "https://sbs.mof.gov.cn/zhengcefabu/",
        "source": "财政部社会保障司_政策发布",
    },
    {
        "name": "财政部科教和文化司_工作动态",
        "url": "https://jkw.mof.gov.cn/gongzuodongtai/",
        "source": "财政部科教和文化司_工作动态",
    },
    {
        "name": "财政部科教和文化司_工作通知",
        "url": "https://jkw.mof.gov.cn/gongzuotongzhi/",
        "source": "财政部科教和文化司_工作通知",
    },
    {
        "name": "财政部科教和文化司_政策发布",
        "url": "https://jkw.mof.gov.cn/zhengcefabu/",
        "source": "财政部科教和文化司_政策发布",
    },
]


def _page_url(base_url, page_index):
    if page_index == 0:
        return base_url
    return urljoin(base_url, f"index_{page_index}.htm")


def _extract_list_items(soup, page_url, metrics):
    ul_element = soup.select_one("ul.liBox")
    if not ul_element:
        metrics.errors.append(f"未找到目标列表 ul.liBox: {page_url}")
        return []

    items = []
    for li in ul_element.find_all("li", recursive=False):
        metrics.raw_item_count += 1
        link = li.find("a")
        date_node = li.find("span")
        title = ""
        href = ""
        if link:
            title = (link.get("title") or link.get_text(" ", strip=True)).strip()
            href = (link.get("href") or "").strip()
        pub_at = parse_date(date_node.get_text(" ", strip=True) if date_node else "")

        if not title or not href or not pub_at:
            metrics.invalid_item_count += 1
            metrics.errors.append(
                f"列表记录核心字段缺失: {page_url} - {title or href or '未知条目'}"
            )
            continue

        metrics.valid_item_count += 1
        items.append(
            {
                "title": title,
                "url": urljoin(page_url, href),
                "pub_at": pub_at,
            }
        )
    return items


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        content_element = (
            soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="my_doccontent")
            or soup.find("div", class_="my_conboxzw")
            or soup.find("div", class_="content")
        )
        if not content_element:
            return ""
        lines = [
            line.strip()
            for line in content_element.get_text("\n", strip=True).splitlines()
            if line.strip()
        ]
        return "\n".join(lines)
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_single_config(config):
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()
    seen_urls = set()

    try:
        for page_index in range(MAX_PAGES):
            page_url = _page_url(config["url"], page_index)
            try:
                response = session.get(page_url, headers=HEADERS, timeout=30)
                if response.status_code == 404:
                    break
                response.raise_for_status()
            except Exception as exc:
                if page_index == 0:
                    metrics.errors.append(f"列表页抓取失败: {page_url} - {exc}")
                break

            soup = BeautifulSoup(response.content, "html.parser")
            page_items = _extract_list_items(soup, page_url, metrics)
            if not page_items:
                if page_index == 0 and not metrics.errors:
                    metrics.errors.append(f"列表页未解析到有效记录: {page_url}")
                break

            page_has_target_or_newer = False
            page_oldest_date = None
            for item in page_items:
                if item["url"] in seen_urls:
                    metrics.duplicate_policy_count += 1
                    continue
                seen_urls.add(item["url"])

                pub_at = item["pub_at"]
                if len(latest_items) < 5:
                    latest_items.append({"title": item["title"], "pub_at": pub_at})

                if is_target_date(pub_at, target_from, target_to):
                    page_has_target_or_newer = True
                    content = _extract_content(session, item["url"], metrics)
                    policies.append(
                        {
                            "title": item["title"],
                            "url": item["url"],
                            "pub_at": pub_at,
                            "content": content,
                            "selected": False,
                            "category": CATEGORY,
                            "source": config["source"],
                        }
                    )
                else:
                    metrics.filtered_count += 1
                    if pub_at > target_to:
                        page_has_target_or_newer = True

                if page_oldest_date is None or pub_at < page_oldest_date:
                    page_oldest_date = pub_at

            if page_oldest_date and page_oldest_date < target_from and not page_has_target_or_newer:
                break

    except Exception as exc:
        metrics.errors.append(f"{config['name']} 抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(1 for item in policies if not item.get("content"))
    return policies, latest_items[:5], metrics


def scrape_data():
    all_policies = []
    all_latest_items = []
    combined_metrics = CrawlerMetrics()

    for config in CRAWLER_CONFIGS:
        policies, latest_items, metrics = scrape_single_config(config)
        all_policies.extend(policies)
        all_latest_items.extend(
            {
                "title": f"{config['name']}：{item['title']}",
                "pub_at": item["pub_at"],
            }
            for item in latest_items
        )
        for field_name in (
            "raw_item_count",
            "valid_item_count",
            "target_date_count",
            "filtered_count",
            "invalid_item_count",
            "empty_content_count",
            "duplicate_policy_count",
        ):
            setattr(
                combined_metrics,
                field_name,
                getattr(combined_metrics, field_name) + getattr(metrics, field_name),
            )
        combined_metrics.errors.extend(metrics.errors)

    return all_policies, all_latest_items[:5], combined_metrics


def create_runner(config):
    def runner():
        data, latest_items, metrics = scrape_single_config(config)
        processed_items, api_push_result = save_to_policy(data, config["source"])
        return CrawlerRunResult(
            items=processed_items,
            latest_items=latest_items,
            metrics=metrics,
            api_push_result=api_push_result,
        )

    return runner


def module_for(config_name):
    config = next(item for item in CRAWLER_CONFIGS if item["name"] == config_name)
    return SimpleNamespace(
        __file__=__file__,
        __name__=__name__,
        TARGET_URL=config["url"],
    )


def run():
    data, latest_items, metrics = scrape_data()
    processed_items, api_push_result = save_to_policy(data, "财政部多栏目")
    return CrawlerRunResult(
        items=processed_items,
        latest_items=latest_items,
        metrics=metrics,
        api_push_result=api_push_result,
    )


for config in CRAWLER_CONFIGS:
    fn_name = f"run_{config['name'].replace(' ', '_').replace('/', '_')}"
    globals()[fn_name] = create_runner(config)


if __name__ == "__main__":
    for crawler_config in CRAWLER_CONFIGS:
        items, latest, crawler_metrics = scrape_single_config(crawler_config)
        print(crawler_config["name"], len(items), crawler_metrics.to_dict())
        for latest_item in latest:
            print(latest_item)
