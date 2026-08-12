# PolicyClaw 2.0 AI 爬虫开发规范

本文件是为 AI Coding 工具和协作者准备的项目级开发约束。新增、迁移或修复单站爬虫前，必须完整阅读本文件，并以当前仓库中的 `crawler_core.py`、`db_utils.py` 和 `crawler_manager.py` 为最终事实来源。

## 1. 任务边界

默认的“新增一个爬虫”任务只允许修改：

1. 新增的单站 `*_crawler.py` 文件；
2. 仅在确有必要时更新 `requirements.txt`。

爬虫由 `crawler_manager.py` 自动发现，不得再为单个爬虫添加手动导入或注册代码。只有在用户明确要求修改发现机制、统一指标、失败补抓或工作流行为时，才允许修改 `crawler_manager.py` 或工作流。

除非用户明确要求，不要重构 `crawler_core.py`、`db_utils.py`、工作流、其他爬虫或数据库结构。不要覆盖、回滚或删除工作区中不属于当前任务的改动。

开始编码前必须确认：目标站点名称、列表页 URL、栏目含义、归属层级、期望抓取内容和是否需要分页。如果无法访问网站，也没有 HTML/JSON 样本，不得凭空编造选择器；应说明未验证部分并请求样本。

## 2. 目录、命名和归属

### 2.1 文件位置

- 中央、国务院、部委及国家级机构：`Ministries/`
- 江苏省政府、省级厅局和省级机构：`Jiangsu/`
- 江苏省内的13个地级市和市级机构：`City/`

新文件使用小写 snake_case，并以 `_crawler.py` 结尾，例如：

```text
Ministries/example_zcwj_crawler.py
Jiangsu/jiangsu_example_tzgg_crawler.py
City/Suzhou_example_zcwj_crawler.py
```

一个文件原则上对应一个站点栏目。如果同一站点的多个栏目共用完整抓取逻辑，可以复用内部函数；确需一个文件聚合多个栏目时，文件的 `run()` 必须一次性执行并汇总这些栏目。动态发现按文件执行一次，不会为文件内部的栏目分别创建入口。

三个爬虫目录中的文件名必须全局唯一。`POLICYCLAW_CRAWLER_FILES` 使用不含目录的文件名进行筛选，重名文件会造成补跑选择歧义。

### 2.2 category 固定值

- `Ministries/` 下的爬虫：`category` 必须为 `"中央部委"`
- `Jiangsu/` 下的爬虫：`category` 必须为 `"江苏省本级"`
- `City/` 下的爬虫：`category` 必须为地级市名称，例如 `"南京"或者"连云港"`

不得使用空字符串、网站栏目名或自行创造的新分类。新增其他地域目录时先向项目维护者确认分类值。

### 2.3 模块常量

每个单站爬虫至少定义：

```python
TARGET_URL = "https://example.gov.cn/list/"
SOURCE_NAME = "机构名称_栏目名称"
```

`TARGET_URL` 会被 `crawler_manager.py` 用于统一输出。`SOURCE_NAME` 是动态发现后的用户可见名称，必须稳定、可读且在所有爬虫中唯一。缺少 `SOURCE_NAME` 时管理器只能回退显示模块文件名；新爬虫不得依赖此回退。

## 3. 强制接口

每个新爬虫必须提供：

```python
def scrape_data():
    """返回 (policies, latest_items, metrics)。"""


def run():
    """执行抓取、统一保存，并返回 CrawlerRunResult。"""
```

推荐且默认要求使用以下结构化类型：

```python
from crawler_core import CrawlerMetrics, CrawlerRunResult
```

不要为新爬虫复制旧式返回值模式。不要只返回裸列表，也不要依赖管理器从自定义打印文本中猜测指标。

`run()` 必须调用一次统一保存入口：

```python
from db_utils import save_to_policy

processed_items, api_push_result = save_to_policy(data, SOURCE_NAME)
```

单站爬虫不得直接创建 Supabase 客户端、直接读写数据表或自行调用业务 API。

## 4. 标准数据字段

每条目标日期政策至少返回：

```python
{
    "title": "完整标题",
    "url": "https://example.gov.cn/detail/123.html",
    "pub_at": pub_at,
    "content": "正文纯文本",
    "selected": False,
    "category": "中央部委",  # 或“江苏省本级”或者“无锡”、“盐城”等
    "source": SOURCE_NAME,
}
```

字段要求：

| 字段 | 要求 |
| --- | --- |
| `title` | 必填，去除首尾空白，不能用栏目名代替文章标题 |
| `url` | 必填，必须转换成绝对 URL |
| `pub_at` | 必填，推荐为 `datetime.date`；也可为可解析的标准日期字符串 |
| `content` | 尽量抓取详情页正文；失败时允许为空，但必须计入指标或错误 |
| `selected` | 固定为 `False` |
| `category` | 严格使用本文件规定的固定值 |
| `source` | 使用稳定的 `SOURCE_NAME` |

不要自行提供 `id`、`created_at`、`crawled_at` 或 `policy_key`：

- `policy_key` 由 `crawler_core.normalize_policy_item()` 自动生成；
- `created_at` 由 Supabase 数据库默认值 `now()` 生成；
- 数据库可写字段由 `db_utils.POLICY_TABLE_FIELDS` 统一控制。

只有在网站真实提供且项目维护者明确要求时才解析 `doc_no`、`issuer`、`attachments`。当前数据库写入层不会持久化这些扩展字段，不得虚构值。

## 5. 统一日期窗口

严禁在单站爬虫中写死“今天”“昨天”“最近 7 天”，也不要自行读取 `CRAWL_DATE*` 环境变量。

必须使用：

```python
from crawler_core import get_crawl_date_window, is_target_date, parse_date

target_date_from, target_date_to = get_crawl_date_window()
pub_at = parse_date(raw_date)

if not is_target_date(pub_at, target_date_from, target_date_to):
    metrics.filtered_count += 1
    continue
```

统一核心支持三种运行模式：

- `sliding_window`：滑动窗口；
- `single_date`：单日补跑；
- `date_range`：指定日期段补跑。

新增爬虫必须同时兼容这三种模式。不得只适配默认滑动窗口。

如果列表按发布日期倒序分页，可以在确认当前页最旧有效日期早于 `target_date_from` 后停止翻页。不能只抓第一页并假设日期段一定落在第一页。

发布日期无法解析的候选项不能进入 `policies`。应增加 `invalid_item_count`，并在 `metrics.errors` 中保留简洁诊断；不要用当前日期代替缺失发布日期。

## 6. 抓取和解析要求

### 6.1 请求

- 所有网络请求必须设置合理的 `timeout`；列表页通常 30 秒，详情页通常 15 秒；
- 使用清晰的 `User-Agent`；
- HTTP 错误使用 `raise_for_status()`；
- 相对链接使用 `urllib.parse.urljoin()`；
- 优先复用 `requests.Session()`；
- 不得关闭 TLS 校验来掩盖证书问题；确有特殊情况必须说明风险；
- 不得在代码中写入 Cookie、Token、账号、密码或任何 Secret。

### 6.2 页面类型选择

按以下顺序选择实现方式：

1. 官方 JSON/API 数据；
2. 服务端渲染 HTML；
3. 页面内嵌 JSON 或 XML；
4. 只有确认前三种不可用时，才使用 Selenium、Playwright 或其他浏览器方案。

不要为了方便无条件引入浏览器。新增依赖前检查 `requirements.txt`，确需新增时一并更新并说明原因。

### 6.3 列表页

列表页必须提取并验证：标题、链接、发布日期。候选节点数量用于 `raw_item_count`，不要用页面全部 `li`、`a` 的数量冒充有效政策记录数量。

`latest_items` 用于统一展示页面最新 5 条，格式为：

```python
{"title": title, "pub_at": pub_at}
```

它应来自列表页整体数据，不能只包含目标日期数据。保持网页的“最新在前”顺序；若页面顺序不可靠，应按有效发布日期倒序整理。

### 6.4 详情页正文

- 只保留正文主体，排除导航、页眉页脚、分享按钮、相关推荐等噪声；
- 使用 `get_text("\n", strip=True)` 等方式保留段落边界；
- 正文请求失败不能导致整个爬虫退出，应记录到 `metrics.errors` 并继续处理其他文章；
- 不要随意截断政策正文；如果因外部接口限制必须截断，应先获得维护者确认；
- 空正文允许入库，但必须增加 `empty_content_count`。

## 7. 运行指标

必须创建并维护 `CrawlerMetrics`：

```python
metrics = CrawlerMetrics()
```

指标含义：

| 指标 | 含义 |
| --- | --- |
| `raw_item_count` | 列表页发现的候选政策记录数 |
| `valid_item_count` | 成功解析出标题、URL、发布日期的记录数 |
| `target_date_count` | 位于目标日期窗口内的记录数 |
| `filtered_count` | 日期有效但不在目标窗口内的记录数 |
| `invalid_item_count` | 结构异常、核心字段缺失或日期无法解析的记录数 |
| `empty_content_count` | 目标日期数据中正文为空的记录数 |
| `duplicate_policy_count` | 通常由核心层补充；单站内部主动去重时也应累计 |
| `errors` | 简短、可定位的诊断信息列表，不包含 Secret |

抓取完成后至少设置：

```python
metrics.target_date_count = len(policies)
metrics.empty_content_count = sum(
    1 for item in policies if not item.get("content")
)
```

单条失败应隔离处理，一个坏条目不能中断整站。列表页整体失败时，返回空数据和包含错误信息的 metrics，让管理器继续执行其他爬虫。

## 8. 保存、API 和飞书开关

当前外部动作由公共层独立控制：

- `POLICYCLAW_ENABLE_SUPABASE_WRITE`
- `POLICYCLAW_ENABLE_API_PUSH`
- `POLICYCLAW_ENABLE_FEISHU_NOTIFY`
- `POLICYCLAW_VERBOSE_CRAWLER_LOG`

新爬虫不要读取这些开关，也不要使用旧的 `external_send_enabled()` 判断写入结果。只调用 `save_to_policy()`，由 `db_utils.py` 和 `crawler_manager.py` 处理开关。

Supabase 和 API 是独立动作：即使关闭 Supabase，API 仍可能开启。不得通过 `if saved_items` 等逻辑把 API 与数据库写入重新耦合。

数据库使用 `policy_key` 唯一约束和 upsert 防止两个 Runner 重复写入同一政策。`public.policyclaw2` 还必须启用 `preserve_policyclaw2_nonempty_content` 触发器：当数据库已有非空正文时，后续空字符串或空值不得覆盖原正文。对应 SQL 留档为 `supabase_preserve_nonempty_content.sql`。不得在单站爬虫中绕过统一保存入口或自行实现覆盖规则。

## 9. 统一输出归属

最终用户可见模板由 `crawler_manager.py` 统一打印。单站爬虫不得重复打印以下内容：

- `📦 开始执行爬虫`
- `🔗 目标网址`
- 最终“成功抓取/过滤掉/页面最新5条”汇总
- Supabase、API 的最终状态
- `💾 写入数据库`
- 分隔线

单站内部可以打印必要的诊断日志，例如分页进度、接口降级或详情页异常。这些日志会被管理器捕获，并仅在“输出每个爬虫的原始日志”开启时展示。

预期的最终模板类似：

```text
📦 开始执行爬虫: 站点名称
🔗 目标网址: https://example.gov.cn/list/
----------------------------------------
✅ 站点名称爬虫：成功抓取 3 条目标日期数据
⏭️  过滤掉 20 条非目标日期的数据
📊 页面最新5条是：
✅ 示例标题 2026-07-15
...
✅ 站点名称：成功写入 3 条数据到 Supabase
✅ 站点名称：成功推送 3 条数据到API
💾 写入数据库: 3 条
----------------------------------------
```

## 10. 推荐的新爬虫骨架

以下骨架展示项目接口，选择器和响应结构必须根据真实网站修改，不能原样照抄占位选择器。

```python
from urllib.parse import urljoin

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


TARGET_URL = "https://example.gov.cn/list/"
SOURCE_NAME = "机构名称_栏目名称"
CATEGORY = "中央部委"  # Jiangsu 下改为“江苏省本级”

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


def _extract_content(session, article_url, metrics):
    try:
        response = session.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        element = soup.select_one("真实正文选择器")
        return element.get_text("\n", strip=True) if element else ""
    except Exception as exc:
        metrics.errors.append(f"详情页抓取失败: {article_url} - {exc}")
        return ""


def scrape_data():
    policies = []
    latest_items = []
    metrics = CrawlerMetrics()
    target_from, target_to = get_crawl_date_window()
    session = requests.Session()

    try:
        response = session.get(TARGET_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        nodes = soup.select("真实列表项选择器")
        metrics.raw_item_count = len(nodes)

        for node in nodes:
            try:
                link = node.select_one("真实标题链接选择器")
                title = link.get_text(" ", strip=True) if link else ""
                href = (link.get("href") or "").strip() if link else ""
                pub_at = parse_date(node.select_one("真实日期选择器").get_text(strip=True))

                if not title or not href or not pub_at:
                    metrics.invalid_item_count += 1
                    continue

                article_url = urljoin(TARGET_URL, href)
                metrics.valid_item_count += 1
                latest_items.append({"title": title, "pub_at": pub_at})

                if not is_target_date(pub_at, target_from, target_to):
                    metrics.filtered_count += 1
                    continue

                policies.append(
                    {
                        "title": title,
                        "url": article_url,
                        "pub_at": pub_at,
                        "content": _extract_content(
                            session, article_url, metrics
                        ),
                        "selected": False,
                        "category": CATEGORY,
                        "source": SOURCE_NAME,
                    }
                )
            except Exception as exc:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"列表记录解析失败: {exc}")
    except Exception as exc:
        metrics.errors.append(f"列表页抓取失败: {exc}")

    metrics.target_date_count = len(policies)
    metrics.empty_content_count = sum(
        1 for item in policies if not item.get("content")
    )
    return policies, latest_items[:5], metrics


def run():
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
```

对于真实站点，应增强日期节点缺失处理、分页、JSON 接口解析和正文选择器，不得保留“真实选择器”等占位文本。

## 11. 自动发现与失败补抓

`crawler_manager.py` 会自动扫描以下目录：

- `Ministries/`
- `Jiangsu/`
- `City/`

文件只有同时满足以下条件才会成为爬虫入口：

1. 直接位于上述目录；
2. 文件名以 `_crawler.py` 结尾；
3. 模块可以成功导入；
4. 模块提供可调用的 `run()`。

禁止为新增爬虫修改管理器并添加手动 `import` 或 `register_crawler()`。动态发现优先使用模块的 `SOURCE_NAME` 作为显示名称，使用 `TARGET_URL` 作为目标地址，并使用文件名支持 `POLICYCLAW_CRAWLER_FILES` 精确筛选。

GitHub 托管 Runner 每日执行全量抓取后，会在 `results/` 生成：

- `failed_crawlers.json`：包含原运行日期窗口、Run ID 和失败爬虫文件名；
- `failed_crawlers.txt`：仅包含失败爬虫文件名。

以下任一情况会进入失败补抓清单：

- 爬虫入口执行异常；
- `target_date_count` 和 `filtered_count` 均为 0，且 `latest_items` 为空；
- `metrics.errors` 明确包含列表页或列表 API 抓取失败。

Windows 自建 Runner 稍后下载同日 GitHub Runner 的 Artifact，读取原日期窗口，并通过 `POLICYCLAW_CRAWLER_FILES` 只补跑清单中的文件。清单为空时必须跳过抓取，不能把空筛选误解释为全量运行。定时补抓默认开启 Supabase、API 和飞书；手动触发仍服从工作流输入开关。

## 12. 验收要求

AI Coding 工具完成代码后，必须逐项检查：

- [ ] 文件位于正确目录，文件名以 `_crawler.py` 结尾；
- [ ] 定义了 `TARGET_URL`、`SOURCE_NAME`、`scrape_data()` 和 `run()`；
- [ ] category 严格等于 `中央部委` 或 `江苏省本级`；
- [ ] 使用 `get_crawl_date_window()`、`parse_date()`、`is_target_date()`；
- [ ] 单日、日期段、滑动窗口三种模式均兼容；
- [ ] 每个请求都有 timeout，URL 使用 `urljoin()` 规范化；
- [ ] 不含账号、Cookie、Token、密钥和项目 URL；
- [ ] 不自行生成 `policy_key`、`created_at`；
- [ ] 指标含义正确，`latest_items` 来自整个列表而非目标窗口；
- [ ] `run()` 只通过 `save_to_policy()` 执行保存和 API 推送；
- [ ] 返回 `CrawlerRunResult`，不自行打印统一最终模板；
- [ ] 未在 `crawler_manager.py` 中添加单站导入或注册代码；
- [ ] 文件可被动态发现一次，`SOURCE_NAME` 与文件名在全仓库无歧义；
- [ ] 使用 `POLICYCLAW_CRAWLER_FILES=<file_name>` 时只选中预期文件；
- [ ] 没有修改无关文件或覆盖用户现有改动；
- [ ] 没有遗留占位 URL、占位选择器、测试数据或调试断点。

至少执行以下本地检查：

```bash
python -m py_compile path/to/new_crawler.py crawler_core.py crawler_manager.py db_utils.py
python -c "from path.to import new_crawler; print(new_crawler.TARGET_URL)"
POLICYCLAW_CRAWLER_FILES=new_crawler.py python -c "import crawler_manager as c; m=c.CrawlerManager(); m.discover_crawlers(); m.validate_crawler_selection(); print([(x[0], x[3]) for x in m.crawlers])"
git diff --check
```

在不真实写入外部系统的情况下测试单站爬虫时，明确关闭三个外部动作：

```text
POLICYCLAW_ENABLE_SUPABASE_WRITE=0
POLICYCLAW_ENABLE_API_PUSH=0
POLICYCLAW_ENABLE_FEISHU_NOTIFY=0
```

至少测试一个单日窗口和一个日期段窗口。测试报告要说明：实际访问的 URL、列表候选数、有效数、目标日期数、过滤数、正文为空数、最新 5 条，以及任何未验证风险。

## 13. AI 最终交付格式

完成新增爬虫后，AI 应向维护者简要报告：

1. 新增和修改了哪些文件；
2. 抓取的站点、栏目和数据来源；
3. 使用 HTML、JSON 还是浏览器方案；
4. category 和 source 设置；
5. 日期窗口与分页策略；
6. 自动发现结果和文件名筛选结果；
7. 执行过的测试及结果；
8. 仍存在的站点访问、反爬、正文解析或分页风险。

不得声称“已验证成功”，除非真实运行过相应检查并看到成功结果。
