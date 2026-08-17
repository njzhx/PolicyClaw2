import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

from crawler_core import feishu_notify_enabled


MAX_ABNORMAL_ITEMS = 25
ABNORMAL_GROUPS = (
    ("execution_failure", "执行失败"),
    ("fetch_failure", "列表抓取失败"),
    ("parse_failure", "解析异常"),
    ("list_empty", "列表为空"),
    ("suspect", "未获取到列表信息"),
)
LIST_ERROR_MARKERS = (
    "列表页抓取失败",
    "列表页HTTP错误",
    "API抓取失败",
    "list request failed",
    "list page failed",
)


def _actions_run_url():
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "").strip("/")
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return ""


def _short_text(value, limit=100):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _classify_abnormal(result):
    metrics = result.get("metrics") or {}
    errors = [str(error) for error in (metrics.get("errors") or []) if error]
    if result.get("status") == "error":
        detail = result.get("error_message") or (errors[0] if errors else "未知错误")
        return "execution_failure", _short_text(detail)

    list_error = next(
        (error for error in errors if any(marker in error for marker in LIST_ERROR_MARKERS)),
        None,
    )
    if list_error:
        return "fetch_failure", _short_text(list_error)

    raw_count = int(metrics.get("raw_item_count") or 0)
    valid_count = int(metrics.get("valid_item_count") or 0)
    target_count = int(metrics.get("target_date_count", result.get("crawl_count", 0)) or 0)
    filtered_count = int(metrics.get("filtered_count", result.get("filter_count", 0)) or 0)
    if raw_count == 0:
        return "list_empty", "raw_item_count 为 0"
    if valid_count == 0:
        return "parse_failure", f"发现 {raw_count} 条，解析有效数据 0 条"
    if target_count == 0 and filtered_count == 0 and not result.get("latest_items"):
        return "suspect", "目标日期与过滤数量均为 0，且无最新条目"
    return None


def build_crawler_summary(results, start_time, end_time):
    abnormal = []
    total_inserted = 0
    total_updated = 0
    verified_storage_count = 0
    unverified_storage_count = 0
    for name, result in results.items():
        classified = _classify_abnormal(result)
        if classified:
            group, detail = classified
            abnormal.append({
                "name": name,
                "group": group,
                "detail": detail,
                "target_url": result.get("target_url") or "",
            })

        storage_result = result.get("storage_result")
        if not isinstance(storage_result, dict):
            continue
        status = storage_result.get("status")
        if status in {"success", "partial"}:
            if storage_result.get("counts_verified") is True:
                verified_storage_count += 1
                total_inserted += int(storage_result.get("inserted_count") or 0)
                total_updated += int(storage_result.get("updated_count") or 0)
            else:
                unverified_storage_count += 1
        elif status == "error":
            unverified_storage_count += 1

    return {
        "started_at": start_time.isoformat(),
        "ended_at": end_time.isoformat(),
        "duration_seconds": max(0, int((end_time - start_time).total_seconds())),
        "total_count": len(results),
        "normal_count": len(results) - len(abnormal),
        "abnormal_count": len(abnormal),
        "abnormal": abnormal,
        "inserted_count": total_inserted,
        "updated_count": total_updated,
        "verified_storage_count": verified_storage_count,
        "unverified_storage_count": unverified_storage_count,
        "actions_run_url": _actions_run_url(),
    }


def write_crawler_summary(results, start_time, end_time, output_path):
    summary = build_crawler_summary(results, start_time, end_time)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _format_duration(seconds):
    minutes, seconds = divmod(max(0, int(seconds or 0)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


class OutputCapturer:
    """控制台输出捕获器"""

    def __init__(self):
        self.captured_output = []
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

    def start_capture(self):
        """开始捕获输出"""
        self.captured_output = []
        self.string_buffer = StringIO()
        sys.stdout = self.string_buffer
        sys.stderr = self.string_buffer

    def stop_capture(self):
        """停止捕获输出并返回捕获的内容"""
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        output = self.string_buffer.getvalue()
        self.captured_output.append(output)
        return output

    def get_full_output(self):
        """获取所有捕获的输出"""
        return ''.join(self.captured_output)


class FeishuNotifier:
    """飞书机器人通知器"""

    def __init__(self, webhook_url=None):
        """初始化飞书通知器

        Args:
            webhook_url: 飞书机器人 webhook 地址，如果为 None 则从环境变量 FEISHU_BOT_WEBHOOK 获取
        """
        self.webhook_url = webhook_url or os.getenv('FEISHU_BOT_WEBHOOK')
        self.allow_feishu_notify = feishu_notify_enabled()
        self.enabled = bool(self.webhook_url)
        self.output_capturer = OutputCapturer()

        if not self.enabled:
            print("⚠️  飞书机器人未配置（FEISHU_BOT_WEBHOOK 环境变量未设置）")
        elif not self.allow_feishu_notify:
            print("🧪 飞书通知已关闭，不会真实发送。设置 POLICYCLAW_ENABLE_FEISHU_NOTIFY=1 后才会发送。")

    def start_capture(self):
        """开始捕获控制台输出"""
        self.output_capturer.start_capture()

    def stop_capture(self):
        """停止捕获控制台输出"""
        return self.output_capturer.stop_capture()

    def send_text(self, text):
        """发送文本消息

        Args:
            text: 文本内容

        Returns:
            bool: 是否发送成功
        """
        if not self.enabled:
            return False

        payload = {
            "msg_type": "text",
            "content": {
                "text": text
            }
        }

        return self._send(payload)

    def send_rich_text(self, title, content):
        """发送富文本消息

        Args:
            title: 标题
            content: 富文本内容列表，格式为 [
                [{"tag": "text", "text": "文本"}, {"tag": "a", "text": "链接", "href": "url"}],
                ...
            ]

        Returns:
            bool: 是否发送成功
        """
        if not self.enabled:
            return False

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content
                    }
                }
            }
        }

        return self._send(payload)

    def send_interactive(self, card):
        """发送交互式卡片消息

        Args:
            card: 卡片内容（dict 格式）

        Returns:
            bool: 是否发送成功
        """
        if not self.enabled:
            return False

        payload = {
            "msg_type": "interactive",
            "card": card
        }

        return self._send(payload)

    def send_summary(self, summary, workflow_status="success"):
        """发送精简工作流摘要，只展开异常爬虫。"""
        if not self.enabled:
            return False

        workflow_status = str(workflow_status or "failure").lower()
        abnormal_count = int(summary.get("abnormal_count") or 0)
        if workflow_status == "success" and abnormal_count == 0:
            icon, status_text = "✅", "GitHub Actions 工作流成功"
        elif workflow_status == "success":
            icon, status_text = "⚠️", "GitHub Actions 成功，但有爬虫异常"
        else:
            icon, status_text = "❌", "GitHub Actions 工作流失败"

        try:
            start = datetime.fromisoformat(summary.get("started_at")).astimezone(
                timezone(timedelta(hours=8))
            )
            start_text = start.strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            start_text = "时间未知"

        total_count = int(summary.get("total_count") or 0)
        normal_count = int(summary.get("normal_count") or 0)
        content = [
            [{"tag": "text", "text": f"{icon} {status_text}"}],
            [{"tag": "text", "text": (
                f"🕒 {start_text}（北京时间）｜"
                f"用时 {_format_duration(summary.get('duration_seconds'))}"
            )}],
            [{"tag": "text", "text": (
                f"📊 爬虫：共 {total_count} 个｜正常 {normal_count} 个｜"
                f"异常 {abnormal_count} 个"
            )}],
        ]

        verified_count = int(summary.get("verified_storage_count") or 0)
        unverified_count = int(summary.get("unverified_storage_count") or 0)
        if verified_count:
            database_text = (
                f"🗄️ 数据库：新增 {int(summary.get('inserted_count') or 0)} 条｜"
                f"更新 {int(summary.get('updated_count') or 0)} 条"
            )
            if unverified_count:
                database_text += f"｜{unverified_count} 个来源未核验"
        elif unverified_count:
            database_text = f"🗄️ 数据库：新增数量无法完整核验（{unverified_count} 个来源）"
        else:
            database_text = "🗄️ 数据库：本轮没有需要核验的写入"
        content.append([{"tag": "text", "text": database_text}])

        abnormal = summary.get("abnormal") or []
        if abnormal:
            content.append([{"tag": "text", "text": "──────────"}])
            displayed_count = 0
            for group_key, group_label in ABNORMAL_GROUPS:
                group_items = [item for item in abnormal if item.get("group") == group_key]
                if not group_items:
                    continue
                content.append([{"tag": "text", "text": f"⚠️ {group_label}（{len(group_items)}）"}])
                for item in group_items:
                    if displayed_count >= MAX_ABNORMAL_ITEMS:
                        break
                    row = [{"tag": "text", "text": "• "}]
                    if item.get("target_url"):
                        row.append({
                            "tag": "a",
                            "text": _short_text(item.get("name"), 60),
                            "href": item["target_url"],
                        })
                    else:
                        row.append({"tag": "text", "text": _short_text(item.get("name"), 60)})
                    row.append({"tag": "text", "text": f"：{_short_text(item.get('detail'))}"})
                    content.append(row)
                    displayed_count += 1
                if displayed_count >= MAX_ABNORMAL_ITEMS:
                    break
            hidden_count = len(abnormal) - displayed_count
            if hidden_count > 0:
                content.append([{"tag": "text", "text": f"……另有 {hidden_count} 个异常爬虫，请查看完整日志"}])
        else:
            content.append([{"tag": "text", "text": "✅ 所有爬虫均正常获取列表信息"}])

        actions_url = summary.get("actions_run_url") or _actions_run_url()
        if actions_url:
            content.append([
                {"tag": "text", "text": "🔗 "},
                {"tag": "a", "text": "查看 GitHub Actions 运行详情", "href": actions_url},
            ])
        return self.send_rich_text("政策爬虫工作流结果", content)

    def send_crawler_result(self, results, start_time, end_time, full_log=None):
        """兼容本地运行：直接构建并发送精简摘要。"""
        summary = build_crawler_summary(results, start_time, end_time)
        return self.send_summary(summary, workflow_status="success")

    def _send(self, payload):
        """发送消息到飞书

        Args:
            payload: 消息 payload

        Returns:
            bool: 是否发送成功
        """
        if not self.allow_feishu_notify:
            print("🧪 DRY-RUN：已生成飞书消息 payload，未真实发送。")
            return True

        try:
            request = Request(
                self.webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get('code') == 0:
                print("✅ 飞书消息发送成功")
                return True
            else:
                print(f"❌ 飞书消息发送失败：{result.get('msg', '未知错误')}")
                return False

        except Exception as e:
            print(f"❌ 飞书消息发送异常：{e}")
            return False


# 全局实例
_notifier = None


def get_notifier():
    """获取飞书通知器全局实例"""
    global _notifier
    if _notifier is None:
        _notifier = FeishuNotifier()
    return _notifier


def send_crawler_result(results, start_time, end_time, full_log=None):
    """发送爬虫执行结果（便捷函数）"""
    notifier = get_notifier()
    return notifier.send_crawler_result(results, start_time, end_time, full_log)


def send_summary_file(summary_file, workflow_status):
    path = Path(summary_file)
    if path.exists():
        summary = json.loads(path.read_text(encoding="utf-8"))
    else:
        now = datetime.now().astimezone()
        summary = build_crawler_summary({}, now, now)
    return get_notifier().send_summary(summary, workflow_status)


def main():
    parser = argparse.ArgumentParser(description="发送 PolicyClaw 飞书工作流摘要")
    parser.add_argument("--summary-file", required=True)
    parser.add_argument("--workflow-status", default="failure")
    args = parser.parse_args()
    if not os.getenv("FEISHU_BOT_WEBHOOK"):
        print("[FEISHU] skipped: FEISHU_BOT_WEBHOOK not configured")
        return
    if not send_summary_file(args.summary_file, args.workflow_status):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
