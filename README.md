# PolicyClaw 2.0

政府政策信息爬虫集合，入口为 `crawler_manager.py`。当前重构重点是：统一字段、结构化运行指标、政策实体去重、日期窗口补跑，以及外部发送 dry-run 保护。

## 运行方式

```bash
python crawler_manager.py
```

默认不会真实写入 Supabase、不会推送业务 API、不会发送飞书消息。需要真实发送时显式设置：

```bash
POLICYCLAW_ENABLE_EXTERNAL_SEND=1 python crawler_manager.py
```

## 日期窗口

- `CRAWL_DATE=2026-05-26`：只补跑某一天。
- `CRAWL_DATE_FROM=2026-05-20` 和 `CRAWL_DATE_TO=2026-05-26`：补跑日期范围。
- `CRAWL_WINDOW_DAYS=7`：未指定日期时使用最近 7 天滑动窗口。

说明：当前爬虫文件已批量接入 `crawler_core.py` 的统一日期窗口能力；后续新增站点也应使用同一套日期窗口和目标日期判定。

## 标准字段

爬虫最终数据建议统一为：

```text
title, url, pub_at, content, source, category, doc_no, issuer, attachments, crawled_at
```

`crawler_core.py` 会补齐字段、规范化 URL、生成 `policy_key`，并按“标准化标题 + 发文字号 + 发布日期”或“标准化标题 + 发布日期”做政策实体去重。

## 外部发送

以下外部副作用默认关闭：

- Supabase 写入
- 业务接口推送
- 每日状态接口推送
- 飞书机器人通知

只有 `POLICYCLAW_ENABLE_EXTERNAL_SEND=1` 时才会真实发送。GitHub Actions 手动触发也提供 `enable_external_send` 开关，默认是 `false`。

业务接口推送还有单独开关：

```bash
POLICYCLAW_ENABLE_API_PUSH=1
```

测试期间保持默认关闭即可。只有同时设置 `POLICYCLAW_ENABLE_EXTERNAL_SEND=1` 和 `POLICYCLAW_ENABLE_API_PUSH=1`，`push_to_api()` 才会真实推送业务接口。

## 运行指标

每个爬虫会汇总结构化 metrics：

```text
raw_item_count, valid_item_count, target_date_count, filtered_count,
invalid_item_count, empty_content_count, duplicate_policy_count
```

异常判断重点：

- `raw_item_count == 0`：列表可能请求失败、被封或选择器失效。
- `valid_item_count == 0`：页面有内容但解析规则可能失效。
- `filtered_count == 0 and target_date_count == 0`：高概率异常。
- `empty_content_count` 升高：详情页正文选择器可能失效。
