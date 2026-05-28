import os
import sys
import requests
import json
from datetime import datetime
from io import StringIO

from crawler_core import external_send_enabled


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
        self.allow_external_send = external_send_enabled()
        self.enabled = bool(self.webhook_url)
        self.output_capturer = OutputCapturer()

        if not self.enabled:
            print("⚠️  飞书机器人未配置（FEISHU_BOT_WEBHOOK 环境变量未设置）")
        elif not self.allow_external_send:
            print("🧪 飞书通知处于 DRY-RUN：不会真实发送。设置 POLICYCLAW_ENABLE_EXTERNAL_SEND=1 后才会发送。")

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

    def send_crawler_result(self, results, start_time, end_time, full_log=None):
        """发送爬虫执行结果

        Args:
            results: 爬虫执行结果字典
            start_time: 开始时间 (datetime)
            end_time: 结束时间 (datetime)
            full_log: 完整的控制台输出日志

        Returns:
            bool: 是否发送成功
        """
        if not self.enabled:
            return False

        # 转换为北京时间（UTC+8）
        from datetime import timezone, timedelta
        tz_utc8 = timezone(timedelta(hours=8))
        beijing_start_time = start_time.astimezone(tz_utc8)

        # 构建富文本内容
        content = []

        # 标题行
        content.append([
            {"tag": "text", "text": f"🚀 爬虫任务 - {beijing_start_time.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）"}
        ])

        # 分隔线
        content.append([{"tag": "text", "text": "==================="}])

        # 显示结构化异常，保留“过滤掉0条”作为异常信号。
        abnormal_crawlers = []
        for name, result in results.items():
            if result['status'] == 'success':
                metrics = result.get('metrics') or {}
                raw_item_count = metrics.get('raw_item_count', 0)
                valid_item_count = metrics.get('valid_item_count', 0)
                target_date_count = metrics.get('target_date_count', result.get('crawl_count', 0))
                filter_count = metrics.get('filtered_count', result.get('filter_count', 0))
                if raw_item_count == 0:
                    reason = "raw_item_count 为 0"
                elif valid_item_count == 0:
                    reason = "valid_item_count 为 0"
                elif filter_count == 0 and target_date_count == 0:
                    reason = "过滤掉 0 条且目标日期 0 条"
                else:
                    reason = ""
                if reason:
                    abnormal_crawlers.append((name, reason))

        if abnormal_crawlers:
            for name, reason in abnormal_crawlers:
                result = results[name]
                target_url = result.get('target_url', '')

                if target_url:
                    content.append([
                        {"tag": "text", "text": "📦 "},
                        {"tag": "a", "text": name, "href": target_url},
                        {"tag": "text", "text": f" {reason}"}
                    ])
                else:
                    content.append([
                        {"tag": "text", "text": f"📦 {name} {reason}"}
                    ])

            # 警告提示
            content.append([{"tag": "text", "text": "疑似爬取内容不成功，请检查。"}])
        else:
            content.append([{"tag": "text", "text": "✅ 所有爬虫均正常获取内容"}])

        # 分隔线
        content.append([{"tag": "text", "text": "==================="}])

        # 添加API推送结果
        api_success_count = 0
        api_error_count = 0
        api_results_added = False

        for name, result in results.items():
            if result.get('status') == 'success' and 'api_push_result' in result:
                api_result = result.get('api_push_result')
                # 确保 api_result 是字典类型，某些爬虫可能返回特殊格式
                if api_result and isinstance(api_result, dict):
                    api_results_added = True
                    status = api_result.get('status', 'unknown')
                    message = api_result.get('message', '')
                    target_url = result.get('target_url', '')

                    if status == 'success':
                        if target_url:
                            content.append([
                                {"tag": "text", "text": "✅ "},
                                {"tag": "a", "text": name, "href": target_url},
                                {"tag": "text", "text": f"：{message}"}
                            ])
                        else:
                            content.append([
                                {"tag": "text", "text": f"✅ {name}：{message}"}
                            ])
                        api_success_count += 1
                    elif status == 'error':
                        if target_url:
                            content.append([
                                {"tag": "text", "text": "❌ "},
                                {"tag": "a", "text": name, "href": target_url},
                                {"tag": "text", "text": f"：{message}"}
                            ])
                        else:
                            content.append([
                                {"tag": "text", "text": f"❌ {name}：{message}"}
                            ])
                        api_error_count += 1
                    else:
                        if target_url:
                            content.append([
                                {"tag": "text", "text": "⚠️ "},
                                {"tag": "a", "text": name, "href": target_url},
                                {"tag": "text", "text": f"：{message}"}
                            ])
                        else:
                            content.append([
                                {"tag": "text", "text": f"⚠️ {name}：{message}"}
                            ])

        if api_results_added:
            content.append([
                {"tag": "text", "text": f"📊 API推送统计: 成功 {api_success_count} 个, 失败 {api_error_count} 个"}
            ])

        # 发送富文本消息（标题需包含飞书机器人关键词"政策"）
        return self.send_rich_text("政策爬虫执行结果", content)

    def _send(self, payload):
        """发送消息到飞书

        Args:
            payload: 消息 payload

        Returns:
            bool: 是否发送成功
        """
        if not self.allow_external_send:
            print("🧪 DRY-RUN：已生成飞书消息 payload，未真实发送。")
            return True

        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
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
