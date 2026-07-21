import os
import time
import sys
from datetime import datetime
from io import StringIO

from crawler_core import (
    adapt_legacy_result,
    feishu_notify_enabled,
    get_crawl_date_window,
)

# 导入飞书通知模块
try:
    from feishu_notifier import send_crawler_result
except ImportError:
    send_crawler_result = None


class DualOutput:
    """双输出流，同时输出到控制台和缓冲区"""

    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.buffer = StringIO()

    def write(self, text):
        self.original_stdout.write(text)
        self.buffer.write(text)

    def flush(self):
        self.original_stdout.flush()
        self.buffer.flush()

    def getvalue(self):
        return self.buffer.getvalue()


# ==========================================
# 爬虫管理系统
# 功能：执行多个爬虫，一个爬虫出错不影响其他爬虫
# ==========================================

class CrawlerManager:
    def __init__(self):
        """初始化爬虫管理器"""
        self.crawlers = []
        self.results = {}
        self.seen_policy_keys = set()
        self.requested_crawler_files = self._parse_crawler_file_selection(
            os.getenv("POLICYCLAW_CRAWLER_FILES", "")
        )
        self.available_crawler_files = {}
        self.verbose_crawler_log = os.getenv("POLICYCLAW_VERBOSE_CRAWLER_LOG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _parse_crawler_file_selection(raw_value):
        """解析逗号分隔的爬虫文件名，并拒绝路径和通配符。"""
        requested = {}
        for raw_token in str(raw_value or "").split(","):
            file_name = raw_token.strip()
            if not file_name:
                continue
            if any(char in file_name for char in ("/", "\\", "*", "?", "[", "]")):
                raise ValueError(
                    f"指定爬虫只能填写文件名，不能包含路径或通配符: {file_name}"
                )
            normalized = file_name.casefold()
            if not normalized.endswith("_crawler.py"):
                raise ValueError(
                    f"指定爬虫文件名必须以 _crawler.py 结尾: {file_name}"
                )
            requested.setdefault(normalized, file_name)
        return requested

    def register_crawler(self, name, crawler_func, crawler_module):
        """注册爬虫

        Args:
            name: 爬虫名称
            crawler_func: 爬虫执行函数
            crawler_module: 爬虫模块对象，用于获取 TARGET_URL
        """
        module_path = str(getattr(crawler_module, "__file__", "") or "")
        module_name = str(getattr(crawler_module, "__name__", "") or "")
        file_name = os.path.basename(module_path) or f"{module_name.rsplit('.', 1)[-1]}.py"
        normalized_file_name = file_name.casefold()
        self.available_crawler_files.setdefault(normalized_file_name, set()).add(module_name)

        if (
            self.requested_crawler_files
            and normalized_file_name not in self.requested_crawler_files
        ):
            return

        target_url = getattr(crawler_module, 'TARGET_URL', '')
        self.crawlers.append((name, crawler_func, target_url))
        if self.verbose_crawler_log:
            if target_url:
                print(f"[REGISTER] {name} ({target_url})")
            else:
                print(f"[REGISTER] {name}")

    def validate_crawler_selection(self):
        """在所有模块注册后校验指定文件，并输出本次筛选结果。"""
        if not self.requested_crawler_files:
            print("[FILTER] 未指定爬虫文件，本次运行全部爬虫")
            return

        unknown = [
            original
            for normalized, original in self.requested_crawler_files.items()
            if normalized not in self.available_crawler_files
        ]
        ambiguous = {
            self.requested_crawler_files[normalized]: modules
            for normalized, modules in self.available_crawler_files.items()
            if normalized in self.requested_crawler_files and len(modules) > 1
        }
        if unknown or ambiguous:
            messages = []
            if unknown:
                messages.append(
                    "未找到或未成功注册以下爬虫文件: " + ", ".join(unknown)
                )
            for file_name, modules in ambiguous.items():
                messages.append(
                    f"爬虫文件名存在跨模块歧义: {file_name} -> "
                    + ", ".join(sorted(modules))
                )
            raise ValueError("；".join(messages))

        selected_names = list(self.requested_crawler_files.values())
        print(f"[FILTER] 指定爬虫文件: {', '.join(selected_names)}")
        print(f"[CRAWLERS] 本次执行爬虫入口: {len(self.crawlers)} 个")

    def _metric_health(self, result):
        if result.get("status") == "error":
            return "ERROR"
        metrics = result.get("metrics") or {}
        raw_count = metrics.get("raw_item_count", 0)
        valid_count = metrics.get("valid_item_count", 0)
        target_count = metrics.get("target_date_count", 0)
        filtered_count = metrics.get("filtered_count", 0)

        if raw_count == 0:
            return "LIST_EMPTY"
        if valid_count == 0:
            return "PARSE_EMPTY"
        if filtered_count == 0 and target_count == 0:
            return "SUSPECT"
        return "OK"

    @staticmethod
    def _print_crawler_header(name, target_url):
        print(f"\n📦 开始执行爬虫: {name}")
        print(f"🔗 目标网址: {target_url or '-'}")
        print("-" * 40)

    def _print_crawler_result(self, name, result, crawler_output=""):
        metrics = result.get("metrics") or {}
        latest_items = result.get("latest_items") or []
        storage_result = result.get("storage_result") or {}
        api_result = result.get("api_push_result")

        if result.get("status") == "success":
            print(f"✅ {name}爬虫：成功抓取 {result.get('crawl_count', 0)} 条目标日期数据")
            print(f"⏭️  过滤掉 {result.get('filter_count', 0)} 条非目标日期的数据")
            print("📊 页面最新5条是：")
            if latest_items:
                for item in latest_items[:5]:
                    title = str(item.get("title") or "未知标题").strip()
                    pub_at = item.get("pub_at") or "未知日期"
                    print(f"✅ {title} {pub_at}")
            else:
                print("⚠️  未解析到可展示的页面最新条目")
        else:
            print(f"❌ {name}爬虫：执行失败 - {result.get('error_message', '未知错误')}")

        storage_status = storage_result.get("status")
        storage_message = storage_result.get("message") or "未获得 Supabase 写入状态"
        if storage_status == "success":
            print(f"✅ {name}：{storage_message}")
        elif storage_status == "dry_run":
            print(f"🧪 {name}：{storage_message}")
        elif storage_status == "error":
            print(f"❌ {name}：{storage_message}")
        else:
            print(f"⚠️  {name}：{storage_message}")

        if isinstance(api_result, dict):
            api_status = api_result.get("status")
            api_message = api_result.get("message") or "未获得 API 推送详情"
            if api_status == "success":
                print(f"✅ {name}：{api_message}")
            elif api_status in {"dry_run", "skipped"}:
                print(f"🧪 {name}：{api_message}")
            else:
                print(f"❌ {name}：{api_message}")
        else:
            print(f"⚠️  {name}：没有 API 推送记录")

        print(f"💾 写入数据库: {result.get('write_count', 0)} 条")

        errors = []
        if result.get("error_message"):
            errors.append(result["error_message"])
        errors.extend((metrics.get("errors") or [])[:3])
        if errors:
            print("⚠️  运行诊断：")
            for error in errors[:3]:
                print(f"   - {str(error)[:220]}")

        if self.verbose_crawler_log and crawler_output.strip():
            print("🧾 原始爬虫日志：")
            for line in crawler_output.strip().splitlines()[:80]:
                print(f"   {line}")
            if len(crawler_output.strip().splitlines()) > 80:
                print("   ... 原始日志已截断")
        print("-" * 40)

    def run_all_crawlers(self):
        """执行所有爬虫

        Returns:
            dict: 各爬虫执行结果
        """
        # 开始捕获输出
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        dual_out = DualOutput(original_stdout)
        dual_err = DualOutput(original_stderr)
        sys.stdout = dual_out
        sys.stderr = dual_err

        start_datetime = datetime.now()
        crawl_date_from, crawl_date_to = get_crawl_date_window()
        print(f"\n[RUN] 开始执行爬虫任务 - {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"[DATE] 目标日期窗口: {crawl_date_from.isoformat()} 至 {crawl_date_to.isoformat()}")
        print(f"[CRAWLERS] 已注册爬虫: {len(self.crawlers)} 个")
        if self.verbose_crawler_log:
            print("[DEBUG] verbose crawler log: ON")
        print("=" * 80)

        total_start_time = time.time()

        total_crawlers = len(self.crawlers)
        for index, (name, crawler_func, target_url) in enumerate(self.crawlers, 1):
            start_time = time.time()
            crawler_output = ""
            self._print_crawler_header(name, target_url)

            try:
                # 创建临时输出缓冲区，用于捕获当前爬虫的输出
                temp_stdout = StringIO()
                temp_stderr = StringIO()
                temp_original_stdout = sys.stdout
                temp_original_stderr = sys.stderr
                sys.stdout = temp_stdout
                sys.stderr = temp_stderr

                try:
                    # 执行爬虫
                    result = crawler_func()
                finally:
                    # 捕获当前爬虫的输出并恢复标准输出
                    crawler_output = temp_stdout.getvalue() + temp_stderr.getvalue()
                    sys.stdout = temp_original_stdout
                    sys.stderr = temp_original_stderr

                # 记录结果
                execution_time = time.time() - start_time

                adapted_result = adapt_legacy_result(result, crawler_output, name)
                data_list = adapted_result["items"]
                metrics = adapted_result["metrics"]
                latest_items = adapted_result.get("latest_items") or []
                storage_result = adapted_result.get("storage_result") or {}
                api_push_result = adapted_result.get("api_push_result")

                global_duplicate_count = 0
                for item in data_list:
                    policy_key = item.get("policy_key")
                    if policy_key and policy_key in self.seen_policy_keys:
                        global_duplicate_count += 1
                    elif policy_key:
                        self.seen_policy_keys.add(policy_key)
                metrics["duplicate_policy_count"] = metrics.get("duplicate_policy_count", 0) + global_duplicate_count

                crawl_count = metrics.get("target_date_count", len(data_list))
                write_count = storage_result.get("saved_count")
                if not isinstance(write_count, int):
                    write_count = len(data_list) - global_duplicate_count
                filter_count = metrics.get("filtered_count", 0)

                self.results[name] = {
                    'status': 'success',
                    'crawl_count': crawl_count,
                    'write_count': write_count,
                    'filter_count': filter_count,
                    'latest_items': latest_items,
                    'metrics': metrics,
                    'execution_time': round(execution_time, 2),
                    'timestamp': datetime.now().isoformat(),
                    'target_url': target_url,
                    'storage_result': storage_result,
                    'api_push_result': api_push_result,
                    'raw_log_line_count': len(crawler_output.splitlines()),
                }

                self._print_crawler_result(name, self.results[name], crawler_output)

            except Exception as e:
                if sys.stdout is not dual_out:
                    try:
                        crawler_output = temp_stdout.getvalue() + temp_stderr.getvalue()
                    except Exception:
                        crawler_output = ""
                    sys.stdout = dual_out
                    sys.stderr = dual_err

                # 捕获异常，确保其他爬虫继续执行
                execution_time = time.time() - start_time
                self.results[name] = {
                    'status': 'error',
                    'crawl_count': 0,
                    'write_count': 0,
                    'error_message': str(e),
                    'metrics': {
                        'raw_item_count': 0,
                        'valid_item_count': 0,
                        'target_date_count': 0,
                        'filtered_count': 0,
                        'invalid_item_count': 0,
                        'empty_content_count': 0,
                        'duplicate_policy_count': 0,
                        'saved_count': 0,
                        'api_push_failed_count': 0,
                        'errors': [str(e)],
                    },
                    'execution_time': round(execution_time, 2),
                    'timestamp': datetime.now().isoformat(),
                    'target_url': target_url,
                    'latest_items': [],
                    'storage_result': {
                        'status': 'error',
                        'saved_count': 0,
                        'message': '爬虫执行失败，未写入 Supabase',
                    },
                    'raw_log_line_count': len(crawler_output.splitlines()),
                }

                self._print_crawler_result(name, self.results[name], crawler_output)

        total_execution_time = time.time() - total_start_time
        end_datetime = datetime.now()

        print("=" * 80)
        print(f"[SUMMARY] 爬虫执行完成 - {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"[TIME] 总执行时间: {round(total_execution_time, 2)} 秒")
        print(f"[CRAWLERS] 执行爬虫数: {len(self.crawlers)}")

        # 统计结果
        success_count = sum(1 for r in self.results.values() if r['status'] == 'success')
        error_count = sum(1 for r in self.results.values() if r['status'] == 'error')

        # 统计总抓取和写入数量
        total_crawl = sum(r.get('crawl_count', 0) for r in self.results.values())
        total_write = sum(r.get('write_count', 0) for r in self.results.values())
        total_raw = sum((r.get('metrics') or {}).get('raw_item_count', 0) for r in self.results.values())
        total_valid = sum((r.get('metrics') or {}).get('valid_item_count', 0) for r in self.results.values())
        total_duplicate = sum((r.get('metrics') or {}).get('duplicate_policy_count', 0) for r in self.results.values())
        total_empty_content = sum((r.get('metrics') or {}).get('empty_content_count', 0) for r in self.results.values())

        print(f"[OK] 成功: {success_count} 个")
        print(f"[ERROR] 失败: {error_count} 个")
        print(f"[DATA] 总抓取数据: {total_crawl} 条")
        print(f"[SAVE] 总写入数据库: {total_write} 条")
        health_counts = {}
        for result in self.results.values():
            health = self._metric_health(result)
            health_counts[health] = health_counts.get(health, 0) + 1
        health_summary = ", ".join(f"{key}={value}" for key, value in sorted(health_counts.items()))

        print(f"[METRICS] 原始条目: {total_raw} 条，有效条目: {total_valid} 条，重复政策: {total_duplicate} 条，正文为空: {total_empty_content} 条")
        print(f"[HEALTH] 健康状态: {health_summary}")

        # 获取完整日志
        full_log = dual_out.getvalue() + dual_err.getvalue()

        # 从self.results收集API推送结果（统一来源，避免名称不匹配问题）
        api_results = []
        api_status_counts = {}

        for crawler_name, result in self.results.items():
            if 'api_push_result' in result and result['api_push_result']:
                api_result = result['api_push_result']
                # 确保 api_result 是字典类型，某些爬虫可能返回特殊格式
                if isinstance(api_result, dict):
                    api_results.append((crawler_name, api_result))
                    status = api_result.get('status', 'unknown')
                    api_status_counts[status] = api_status_counts.get(status, 0) + 1

        # 恢复标准输出
        sys.stdout = original_stdout
        sys.stderr = original_stderr

        # 输出API推送结果
        print("\n[API] API推送结果:")
        print("-" * 40)

        if api_results:
            for crawler_name, api_result in api_results:
                status = api_result.get('status', 'unknown')
                if status == 'success':
                    label = "[OK]"
                elif status in {"skipped", "dry_run"}:
                    label = "[SKIP]"
                else:
                    label = "[ERROR]"
                print(f"{label} {crawler_name}：{api_result.get('message')}")
            api_summary = ", ".join(f"{key}={value}" for key, value in sorted(api_status_counts.items()))
            print(f"[API] API推送统计: {api_summary}")
        else:
            print("[WARN] 没有API推送记录")
        print("-" * 40)

        # 推送每日状态数据到API
        try:
            from db_utils import push_daily_status
            date_str = start_datetime.date().isoformat()
            daily_success_count = total_crawl  # 使用总抓取数量作为成功数
            daily_fail_count = error_count  # 使用失败的爬虫数作为失败数
            print("\n[DAILY] 推送每日状态数据...")
            daily_status_result = push_daily_status(date_str, daily_success_count, daily_fail_count)
            if isinstance(daily_status_result, dict):
                status = daily_status_result.get('status', 'unknown')
                message = daily_status_result.get('message', '')
                if status == 'success':
                    print(f"[OK] 每日状态数据推送成功：{message}")
                elif status in {"skipped", "dry_run"}:
                    print(f"[SKIP] 每日状态数据未真实推送：{message}")
                else:
                    print(f"[ERROR] 每日状态数据推送失败：{message}")
        except Exception as e:
            print(f"[WARN] 推送每日状态数据时发生错误：{e}")

        # 发送飞书通知
        if not feishu_notify_enabled():
            print("\n[FEISHU] skipped: 飞书结果提醒开关未开启")
        elif send_crawler_result and os.getenv("FEISHU_BOT_WEBHOOK"):
            print("\n[FEISHU] 正在发送飞书通知...")
            send_crawler_result(self.results, start_datetime, end_datetime, full_log)
        else:
            print("\n[FEISHU] skipped: FEISHU_BOT_WEBHOOK not configured")

        return self.results

    def get_summary(self):
        """获取执行摘要"""
        if not self.results:
            return "尚未执行爬虫任务"

        summary = []
        for name, result in self.results.items():
            if result['status'] == 'success':
                summary.append(f"[OK] {name}: 抓取 {result['crawl_count']} 条，写入数据库 {result['write_count']} 条")
            else:
                summary.append(f"[ERROR] {name}: 执行失败 - {result['error_message'][:100]}...")

        return "\n".join(summary)

# ==========================================
# 主执行逻辑
# ==========================================
if __name__ == "__main__":
    # 创建爬虫管理器
    try:
        manager = CrawlerManager()
    except ValueError as exc:
        print(f"[ERROR] 指定爬虫参数无效: {exc}")
        raise SystemExit(2)

    # 注册爬虫
    # 注意：这里需要根据实际爬虫模块进行导入和注册

    # 导入中国政府网爬虫
    try:
        from Ministries import gov_crawler
        manager.register_crawler("中国政府网", gov_crawler.run, gov_crawler)
    except ImportError as e:
        print(f"[WARN]  导入中国政府网爬虫失败: {e}")

    # 导入中国政府网政策解读爬虫
    try:
        from Ministries import gov_interpretation_crawler
        manager.register_crawler("中国政府网政策解读", gov_interpretation_crawler.run, gov_interpretation_crawler)
    except ImportError as e:
        print(f"[WARN]  导入中国政府网政策解读爬虫失败: {e}")

    # 导入国务院文件爬虫
    try:
        from Ministries import gov_zcwj_crawler
        manager.register_crawler("国务院文件", gov_zcwj_crawler.run, gov_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国务院文件爬虫失败: {e}")

    # 导入教育部文件爬虫
    try:
        from Ministries import moe_wj_crawler
        manager.register_crawler("教育部文件", moe_wj_crawler.run, moe_wj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入教育部文件爬虫失败: {e}")

    # 导入科技部政策解读爬虫
    try:
        from Ministries import most_zjgx_crawler
        manager.register_crawler("科技部政策解读", most_zjgx_crawler.run, most_zjgx_crawler)
    except ImportError as e:
        print(f"[WARN]  导入科技部政策解读爬虫失败: {e}")

    # 导入科技部规范性文件爬虫
    try:
        from Ministries import most_gfxwj_crawler
        manager.register_crawler("科技部规范性文件", most_gfxwj_crawler.run, most_gfxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入科技部规范性文件爬虫失败: {e}")

    # 导入公安部政策文件爬虫
    try:
        from Ministries import mps_crawler
        manager.register_crawler("公安部政策文件", mps_crawler.run, mps_crawler)
    except ImportError as e:
        print(f"[WARN]  导入公安部政策文件爬虫失败: {e}")

    # 导入民政部政策文件爬虫
    try:
        from Ministries import mca_crawler
        manager.register_crawler("民政部政策文件", mca_crawler.run, mca_crawler)
    except ImportError as e:
        print(f"[WARN]  导入民政部政策文件爬虫失败: {e}")

    # 导入司法部政策文件爬虫
    try:
        from Ministries import moj_crawler
        manager.register_crawler("司法部政策文件", moj_crawler.run, moj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入司法部政策文件爬虫失败: {e}")

    # 导入财政部政策文件爬虫
    try:
        from Ministries import mof_crawler
        manager.register_crawler("财政部政策文件", mof_crawler.run, mof_crawler)
    except ImportError as e:
        print(f"[WARN]  导入财政部政策文件爬虫失败: {e}")

    # 导入财政部通知公告爬虫
    try:
        from Ministries import mof_buling_crawler
        manager.register_crawler("财政部通知公告", mof_buling_crawler.run, mof_buling_crawler)
    except ImportError as e:
        print(f"[WARN]  导入财政部通知公告爬虫失败: {e}")

    # 导入财政部多栏目爬虫
    try:
        from Ministries import mof_multi_crawler
        manager.register_crawler("财政部经济建设司_通知公告", mof_multi_crawler.run_财政部经济建设司_通知公告, mof_multi_crawler)
        manager.register_crawler("财政部经济建设司_政策法规", mof_multi_crawler.run_财政部经济建设司_政策法规, mof_multi_crawler)
        manager.register_crawler("财政部农业农村司_政策发布", mof_multi_crawler.run_财政部农业农村司_政策发布, mof_multi_crawler)
        manager.register_crawler("财政部社会保障司_工作动态", mof_multi_crawler.run_财政部社会保障司_工作动态, mof_multi_crawler)
        manager.register_crawler("财政部科教和文化司_工作动态", mof_multi_crawler.run_财政部科教和文化司_工作动态, mof_multi_crawler)
        manager.register_crawler("财政部科教和文化司_工作通知", mof_multi_crawler.run_财政部科教和文化司_工作通知, mof_multi_crawler)
        manager.register_crawler("财政部科教和文化司_政策发布", mof_multi_crawler.run_财政部科教和文化司_政策发布, mof_multi_crawler)
    except ImportError as e:
        print(f"[WARN]  导入财政部多栏目爬虫失败: {e}")

    # 导入人社部政策文件爬虫
    try:
        from Ministries import mohrss_crawler
        manager.register_crawler("人社部政策文件", mohrss_crawler.run, mohrss_crawler)
    except ImportError as e:
        print(f"[WARN]  导入人社部政策文件爬虫失败: {e}")

    # 导入自然资源部政策文件爬虫
    try:
        from Ministries import mnr_crawler
        manager.register_crawler("自然资源部政策文件", mnr_crawler.run, mnr_crawler)
    except ImportError as e:
        print(f"[WARN]  导入自然资源部政策文件爬虫失败: {e}")

    # 导入生态环境部爬虫
    try:
        from Ministries import mee_crawler
        manager.register_crawler("生态环境部", mee_crawler.run, mee_crawler)
    except ImportError as e:
        print(f"[WARN]  导入生态环境部爬虫失败: {e}")

    # 导入国家发改委爬虫
    try:
        from Ministries import ndrc_crawler
        manager.register_crawler("国家发改委", ndrc_crawler.run, ndrc_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家发改委爬虫失败: {e}")

    # 导入人民网财经爬虫
    # try:
    #     from Ministries import people_finance_crawler
    #     manager.register_crawler("人民网财经", people_finance_crawler.run, people_finance_crawler)
    # except ImportError as e:
    #     print(f"[WARN]  导入人民网财经爬虫失败: {e}")

    # 注册 mubiao.md 中的16个新爬虫
    try:
        from Ministries import miit_wjk_crawler
        manager.register_crawler("工信部_文件库", miit_wjk_crawler.run, miit_wjk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入工信部_文件库爬虫失败: {e}")

    try:
        from Ministries import miit_zcjd_crawler
        manager.register_crawler("工信部_政策解读", miit_zcjd_crawler.run, miit_zcjd_crawler)
    except ImportError as e:
        print(f"[WARN]  导入工信部_政策解读爬虫失败: {e}")

    try:
        from Ministries import nda_zwgk_crawler
        manager.register_crawler("数据局_政务公开", nda_zwgk_crawler.run, nda_zwgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入数据局_政务公开爬虫失败: {e}")

    try:
        from Ministries import mohurd_wjk_crawler
        manager.register_crawler("住建部_文件库", mohurd_wjk_crawler.run, mohurd_wjk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入住建部_文件库爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gov_zxwj_crawler
        manager.register_crawler("省政府_最新文件", jiangsu_gov_zxwj_crawler.run, jiangsu_gov_zxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省政府_最新文件爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gov_zcjd_crawler
        manager.register_crawler("省政府_政策解读", jiangsu_gov_zcjd_crawler.run, jiangsu_gov_zcjd_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省政府_政策解读爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gov_gb_crawler
        manager.register_crawler("省政府_省政府公报", jiangsu_gov_gb_crawler.run, jiangsu_gov_gb_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省政府_省政府公报爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_fzggw_zcwj_crawler
        manager.register_crawler("省发改委_政策文件", jiangsu_fzggw_zcwj_crawler.run, jiangsu_fzggw_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省发改委_政策文件爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_fzggw_zcjd_crawler
        manager.register_crawler("省发改委_政策解读", jiangsu_fzggw_zcjd_crawler.run, jiangsu_fzggw_zcjd_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省发改委_政策解读爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_fzggw_tzgg_crawler
        manager.register_crawler("省发改委_通知公告", jiangsu_fzggw_tzgg_crawler.run, jiangsu_fzggw_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省发改委_通知公告爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gxt_gsgg_crawler
        manager.register_crawler("省工信厅_公示公告", jiangsu_gxt_gsgg_crawler.run, jiangsu_gxt_gsgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省工信厅_公示公告爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gxt_wjtz_crawler
        manager.register_crawler("省工信厅_文件通知", jiangsu_gxt_wjtz_crawler.run, jiangsu_gxt_wjtz_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省工信厅_文件通知爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_gxt_zcwj_crawler
        manager.register_crawler("省工信厅_政策文件", jiangsu_gxt_zcwj_crawler.run, jiangsu_gxt_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省工信厅_政策文件爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_sjj_zcfb_crawler
        manager.register_crawler("省数据局_政策发布", jiangsu_sjj_zcfb_crawler.run, jiangsu_sjj_zcfb_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省数据局_政策发布爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_sjj_zcjd_crawler
        manager.register_crawler("省数据局_政策解读", jiangsu_sjj_zcjd_crawler.run, jiangsu_sjj_zcjd_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省数据局_政策解读爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_czt_gg_crawler
        manager.register_crawler("财政厅_公告", jiangsu_czt_gg_crawler.run, jiangsu_czt_gg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入财政厅_公告爬虫失败: {e}")

    try:
        from Jiangsu import jiangsu_sjj_gg_crawler
        manager.register_crawler("省数据局_通知公告", jiangsu_sjj_gg_crawler.run, jiangsu_sjj_gg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入省数据局_通知公告爬虫失败: {e}")

    try:
        from Ministries import miit_wjfb_crawler
        manager.register_crawler("工信部_文件发布", miit_wjfb_crawler.run, miit_wjfb_crawler)
    except ImportError as e:
        print(f"[WARN]  导入工信部_文件发布爬虫失败: {e}")

    try:
        from Ministries import miit_gzdt_crawler
        manager.register_crawler("工信部_工作动态", miit_gzdt_crawler.run, miit_gzdt_crawler)
    except ImportError as e:
        print(f"[WARN]  导入工信部_工作动态爬虫失败: {e}")

    # 导入工信部网站tabbox爬虫
    try:
        from Ministries import miit_tabbox_crawler
        manager.register_crawler("工信部_网站tabbox", miit_tabbox_crawler.run, miit_tabbox_crawler)
    except ImportError as e:
        print(f"[WARN]  导入工信部_网站tabbox爬虫失败: {e}")

    # 导入江苏省住房和城乡建设厅爬虫
    try:
        from Jiangsu import jiangsu_zfhcxjst_tf_crawler
        manager.register_crawler("江苏省住房和城乡建设厅", jiangsu_zfhcxjst_tf_crawler.run, jiangsu_zfhcxjst_tf_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省住房和城乡建设厅爬虫失败: {e}")

    # 导入江苏省商务厅意见征集爬虫
    try:
        from Jiangsu import jiangsu_swt_yjzj_crawler
        manager.register_crawler("江苏省商务厅_意见征集", jiangsu_swt_yjzj_crawler.run, jiangsu_swt_yjzj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省商务厅_意见征集爬虫失败: {e}")

    # 导入江苏省商务厅公告通知爬虫
    try:
        from Jiangsu import jiangsu_swt_ggtz_crawler
        manager.register_crawler("江苏省商务厅_公告通知", jiangsu_swt_ggtz_crawler.run, jiangsu_swt_ggtz_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省商务厅_公告通知爬虫失败: {e}")

    # 导入江苏省商务厅政策及公告爬虫
    try:
        from Jiangsu import jiangsu_swt_zcgg_crawler
        manager.register_crawler("江苏省商务厅_政策及公告", jiangsu_swt_zcgg_crawler.run, jiangsu_swt_zcgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省商务厅_政策及公告爬虫失败: {e}")

    # 导入商务部政策发布爬虫
    try:
        from Ministries import mofcom_zcfb_crawler
        manager.register_crawler("商务部_政策发布", mofcom_zcfb_crawler.run, mofcom_zcfb_crawler)
    except ImportError as e:
        print(f"[WARN]  导入商务部_政策发布爬虫失败: {e}")

    # 导入商务部工作通知爬虫
    try:
        from Ministries import mofcom_gztz_crawler
        manager.register_crawler("商务部_工作通知", mofcom_gztz_crawler.run, mofcom_gztz_crawler)
    except ImportError as e:
        print(f"[WARN]  导入商务部_工作通知爬虫失败: {e}")

    # 导入商务部规划计划爬虫
    try:
        from Ministries import mofcom_ghjh_crawler
        manager.register_crawler("商务部_规划计划", mofcom_ghjh_crawler.run, mofcom_ghjh_crawler)
    except ImportError as e:
        print(f"[WARN]  导入商务部_规划计划爬虫失败: {e}")

    # 导入江苏省农业农村厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_agriculture_crawler
        manager.register_crawler("江苏省农业农村厅_通知公告", jiangsu_agriculture_crawler.run, jiangsu_agriculture_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省农业农村厅_通知公告爬虫失败: {e}")

    # 导入江苏省粮食和物资储备局信息公开爬虫
    try:
        from Jiangsu import jiangsu_lsj_xxgk_crawler
        manager.register_crawler(
            "江苏省粮食和物资储备局_信息公开",
            jiangsu_lsj_xxgk_crawler.run,
            jiangsu_lsj_xxgk_crawler,
        )
    except ImportError as e:
        print(f"[WARN]  导入江苏省粮食和物资储备局_信息公开爬虫失败: {e}")

    # 导入江苏省教育厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_jyt_zcwj_crawler
        manager.register_crawler("江苏省教育厅_政策文件", jiangsu_jyt_zcwj_crawler.run, jiangsu_jyt_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省教育厅_政策文件爬虫失败: {e}")

    # 导入江苏省科学技术厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_kxjst_zcwj_crawler
        manager.register_crawler("江苏省科学技术厅_政策文件", jiangsu_kxjst_zcwj_crawler.run, jiangsu_kxjst_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省科学技术厅_政策文件爬虫失败: {e}")

    # 导入江苏省科技厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_kxjst_tzgg_crawler
        manager.register_crawler("江苏省科学技术厅_通知公告", jiangsu_kxjst_tzgg_crawler.run, jiangsu_kxjst_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省科学技术厅_通知公告爬虫失败: {e}")

    # 导入江苏省知产局通知公告爬虫
    try:
        from Jiangsu import jiangsu_zhichanju_tzgg_crawler
        manager.register_crawler("江苏省知识产权局_通知公告", jiangsu_zhichanju_tzgg_crawler.run, jiangsu_zhichanju_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省知识产权局_通知公告爬虫失败: {e}")

     # 导入江苏省国资委政策文件爬虫
    try:
        from Jiangsu import jiangsu_gzw_crawler
        manager.register_crawler("江苏省国资委_政策文件", jiangsu_gzw_crawler.run, jiangsu_gzw_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省国资委_政策文件爬虫失败: {e}")

    # 导入江苏省市场监管局政策文件爬虫
    try:
        from Jiangsu import jiangsu_scjgj_zcwj_crawler
        manager.register_crawler("江苏省市场监管局_政策文件", jiangsu_scjgj_zcwj_crawler.run, jiangsu_scjgj_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省市场监管局_政策文件爬虫失败: {e}")

     # 导入江苏省交通运输厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_jtyst_zcwj_crawler
        manager.register_crawler("江苏省交通运输厅_政策文件", jiangsu_jtyst_zcwj_crawler.run, jiangsu_jtyst_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省交通运输厅_政策文件爬虫失败: {e}")

    # 导入江苏省应急管理厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_yjglt_tzgg_crawler
        manager.register_crawler("江苏省应急管理厅_通知公告", jiangsu_yjglt_tzgg_crawler.run, jiangsu_yjglt_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省应急管理厅_通知公告爬虫失败: {e}")

    # 导入江苏省自然资源厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_zrzy_crawler
        manager.register_crawler("江苏省自然资源厅_政策文件", jiangsu_zrzy_crawler.run, jiangsu_zrzy_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省自然资源厅政策文件爬虫失败: {e}")

    # 导入江苏省民宗委通知公告爬虫
    try:
        from Jiangsu import jiangsu_mzw_tzgg_crawler
        manager.register_crawler("江苏省民宗委_通知公告", jiangsu_mzw_tzgg_crawler.run, jiangsu_mzw_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省民宗委通知公告爬虫失败: {e}")

    # 导入江苏省公安厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_gat_zcwj_crawler
        manager.register_crawler("江苏省公安厅_政策文件", jiangsu_gat_zcwj_crawler.run, jiangsu_gat_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省公安厅政策文件爬虫失败: {e}")

    # 导入江苏省民政厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_mzt_zcwj_crawler
        manager.register_crawler("江苏省民政厅_政策文件", jiangsu_mzt_zcwj_crawler.run, jiangsu_mzt_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省民政厅政策文件爬虫失败: {e}")

    # 导入江苏省人社厅重大民生信息爬虫
    try:
        from Jiangsu import jiangsu_jshrss_zdgkc_crawler
        manager.register_crawler("江苏省人社厅_重大民生信息", jiangsu_jshrss_zdgkc_crawler.run, jiangsu_jshrss_zdgkc_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省人社厅重大民生信息爬虫失败: {e}")

    # 导入江苏省财政厅政策发布爬虫
    try:
        from Jiangsu import jiangsu_czt_zcgg_crawler
        manager.register_crawler("江苏省财政厅_政策发布", jiangsu_czt_zcgg_crawler.run, jiangsu_czt_zcgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省财政厅政策发布爬虫失败: {e}")

    # 导入江苏省生态环境厅通知爬虫
    try:
        from Jiangsu import jiangsu_sthjt_tzgg_crawler
        manager.register_crawler("江苏省生态环境厅_通知", jiangsu_sthjt_tzgg_crawler.run, jiangsu_sthjt_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省生态环境厅通知爬虫失败: {e}")

    # 导入江苏省卫健委规范性文件爬虫
    try:
        from Jiangsu import jiangsu_wjw_zcwj_crawler
        manager.register_crawler("江苏省卫健委_规范性文件", jiangsu_wjw_zcwj_crawler.run, jiangsu_wjw_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省卫健委规范性文件爬虫失败: {e}")

    # 导入江苏省国资委政策文件爬虫
    try:
        from Jiangsu import jiangsu_jsgzw_zcwj_crawler
        manager.register_crawler("江苏省国资委_政策文件", jiangsu_jsgzw_zcwj_crawler.run, jiangsu_jsgzw_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省国资委政策文件爬虫失败: {e}")

    # 导入江苏省市场监管局政策文件爬虫
    try:
        from Jiangsu import jiangsu_scjgj_zcwj_crawler
        manager.register_crawler("江苏省市场监管局_政策文件", jiangsu_scjgj_zcwj_crawler.run, jiangsu_scjgj_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省市场监管局政策文件爬虫失败: {e}")

    # 导入江苏省市场监管局通知公告爬虫
    try:
        from Jiangsu import jiangsu_scjgj_tzgg_crawler
        manager.register_crawler("江苏省市场监管局_通知公告", jiangsu_scjgj_tzgg_crawler.run, jiangsu_scjgj_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省市场监管局通知公告爬虫失败: {e}")

    # 导入江苏省体育局政策文件爬虫
    try:
        from Jiangsu import jiangsu_styj_zcwj_crawler
        manager.register_crawler("江苏省体育局_政策文件", jiangsu_styj_zcwj_crawler.run, jiangsu_styj_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省体育局政策文件爬虫失败: {e}")

    # 导入江苏省医疗保障局政策法规爬虫
    try:
        from Jiangsu import jiangsu_ybj_zcfl_crawler
        manager.register_crawler("江苏省医疗保障局_政策法规", jiangsu_ybj_zcfl_crawler.run, jiangsu_ybj_zcfl_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省医疗保障局政策法规爬虫失败: {e}")

    # 导入江苏省知识产权局政策文件爬虫
    try:
        from Jiangsu import jiangsu_jsip_zcwj_crawler
        manager.register_crawler("江苏省知识产权局_政策文件", jiangsu_jsip_zcwj_crawler.run, jiangsu_jsip_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省知识产权局政策文件爬虫失败: {e}")

    # 导入江苏省国防动员办公室政策文件爬虫
    try:
        from Jiangsu import jiangsu_gfdyb_zcwj_crawler
        manager.register_crawler("江苏省国防动员办公室_政策文件", jiangsu_gfdyb_zcwj_crawler.run, jiangsu_gfdyb_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省国防动员办公室政策文件爬虫失败: {e}")

    # 导入江苏省应急管理厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_yjglt_tzgg_crawler
        manager.register_crawler("江苏省应急管理厅_通知公告", jiangsu_yjglt_tzgg_crawler.run, jiangsu_yjglt_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省应急管理厅通知公告爬虫失败: {e}")

    # 导入江苏省水利厅规范性文件爬虫
    try:
        from Jiangsu import jiangsu_jswater_zcwj_crawler
        manager.register_crawler("江苏省水利厅_规范性文件", jiangsu_jswater_zcwj_crawler.run, jiangsu_jswater_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入江苏省水利厅规范性文件爬虫失败: {e}")

    # ==========================================
    # 南京市爬虫
    # ==========================================

    # 导入南京市政府政策文件爬虫
    try:
        from City import nanjing_zdgk_crawler
        manager.register_crawler(
            "南京市政府_政策文件",
            nanjing_zdgk_crawler.run,
            nanjing_zdgk_crawler,
        )
    except ImportError as e:
        print(f"[WARN]  导入南京市政府政策文件爬虫失败: {e}")

    # 导入南京市教育局部门文件爬虫
    try:
        from City import nanjing_jyj_bmwj_crawler
        manager.register_crawler(
            "南京市教育局_部门文件",
            nanjing_jyj_bmwj_crawler.run,
            nanjing_jyj_bmwj_crawler,
        )
    except ImportError as e:
        print(f"[WARN]  导入南京市教育局部门文件爬虫失败: {e}")

    # 导入交通运输部政府信息公开爬虫
    try:
        from Ministries import mot_fdzdgk_crawler
        manager.register_crawler("交通运输部_政府信息公开", mot_fdzdgk_crawler.run, mot_fdzdgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入交通运输部政府信息公开爬虫失败: {e}")

    # 导入水利部规范性文件爬虫
    try:
        from Ministries import mwr_gfxwj_crawler
        manager.register_crawler("水利部_规范性文件", mwr_gfxwj_crawler.run, mwr_gfxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入水利部规范性文件爬虫失败: {e}")

    # 导入农业农村部政府信息公开爬虫
    try:
        from Ministries import moa_govpublic_crawler
        manager.register_crawler("农业农村部_政府信息公开", moa_govpublic_crawler.run, moa_govpublic_crawler)
    except ImportError as e:
        print(f"[WARN]  导入农业农村部政府信息公开爬虫失败: {e}")

    # 导入文化和旅游部规范性文件爬虫
    try:
        from Ministries import mct_gfxwj_crawler
        manager.register_crawler("文化和旅游部_规范性文件", mct_gfxwj_crawler.run, mct_gfxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入文化和旅游部规范性文件爬虫失败: {e}")

    # 导入文化和旅游部政府信息公开爬虫
    try:
        from Ministries import mct_zwgk_crawler
        manager.register_crawler("文化和旅游部_政府信息公开", mct_zwgk_crawler.run, mct_zwgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入文化和旅游部政府信息公开爬虫失败: {e}")

    # 导入国家卫生健康委员会规范性文件爬虫
    try:
        from Ministries import nhc_gfxwj_crawler
        manager.register_crawler("国家卫生健康委员会_规范性文件", nhc_gfxwj_crawler.run, nhc_gfxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家卫生健康委员会规范性文件爬虫失败: {e}")

    # 导入退役军人事务部规范性文件爬虫
    try:
        from Ministries import mva_gfxwj_crawler
        manager.register_crawler("退役军人事务部_规范性文件", mva_gfxwj_crawler.run, mva_gfxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入退役军人事务部规范性文件爬虫失败: {e}")

    # 导入应急管理部通知公告爬虫
    try:
        from Ministries import mem_tzgg_crawler
        manager.register_crawler("应急管理部_通知公告", mem_tzgg_crawler.run, mem_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入应急管理部通知公告爬虫失败: {e}")

    # 导入国务院国资委政策法规爬虫
    try:
        from Ministries import sasac_zcfg_crawler
        manager.register_crawler("国务院国资委_政策法规", sasac_zcfg_crawler.run, sasac_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国务院国资委政策法规爬虫失败: {e}")

    # 导入市场监管总局政府信息公开爬虫
    try:
        from Ministries import samr_fdzdgk_crawler
        manager.register_crawler("市场监管总局_政府信息公开", samr_fdzdgk_crawler.run, samr_fdzdgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入市场监管总局政府信息公开爬虫失败: {e}")

    # 导入国家知识产权局爬虫
    try:
        from Ministries import cnipa_zcfg_crawler
        manager.register_crawler("国家知识产权局", cnipa_zcfg_crawler.run, cnipa_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家知识产权局爬虫失败: {e}")

    # 导入国家医疗保障局爬虫
    try:
        from Ministries import nhsa_zcfg_crawler
        manager.register_crawler("国家医疗保障局", nhsa_zcfg_crawler.run, nhsa_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家医疗保障局爬虫失败: {e}")

    # 导入国家医疗保障局col109爬虫
    try:
        from Ministries import nhsa_col109_crawler
        manager.register_crawler("国家医疗保障局_通知公告", nhsa_col109_crawler.run, nhsa_col109_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家医疗保障局col109爬虫失败: {e}")

    # 导入中国民用航空局爬虫
    try:
        from Ministries import caac_zcfg_crawler
        manager.register_crawler("中国民用航空局", caac_zcfg_crawler.run, caac_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入中国民用航空局爬虫失败: {e}")

    # 导入国家林业和草原局爬虫
    try:
        from Ministries import forestry_zcfg_crawler
        manager.register_crawler("国家林业和草原局", forestry_zcfg_crawler.run, forestry_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家林业和草原局爬虫失败: {e}")

    # 导入中国气象局爬虫
    try:
        from Ministries import cma_zcfg_crawler
        manager.register_crawler("中国气象局", cma_zcfg_crawler.run, cma_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入中国气象局爬虫失败: {e}")

    # 导入国家互联网信息办公室爬虫
    try:
        from Ministries import cac_zcfg_crawler
        manager.register_crawler("国家互联网信息办公室（规章）", cac_zcfg_crawler.run, cac_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家互联网信息办公室爬虫失败: {e}")

    # 导入国家互联网信息办公室政策文件爬虫
    try:
        from Ministries import cac_zcwj_crawler
        manager.register_crawler("国家互联网信息办公室（政策）", cac_zcwj_crawler.run, cac_zcwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家互联网信息办公室政策文件爬虫失败: {e}")

    # 导入国家药品监督管理局爬虫
    try:
        from Ministries import nmpa_fgwj_crawler
        manager.register_crawler("国家药品监督管理局_法规文件", nmpa_fgwj_crawler.run, nmpa_fgwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家药品监督管理局爬虫失败: {e}")

    # 导入国家消防救援局政务公开爬虫
    try:
        from Ministries import fire_zfxxgk_crawler
        manager.register_crawler("国家消防救援局_政务公开", fire_zfxxgk_crawler.run, fire_zfxxgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家消防救援局政务公开爬虫失败: {e}")

    # 导入国家疾控局政策法规爬虫
    try:
        from Ministries import ndcpa_zcfg_crawler
        manager.register_crawler("国家疾控局_政策法规", ndcpa_zcfg_crawler.run, ndcpa_zcfg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家疾控局政策法规爬虫失败: {e}")

    # 导入国家疾控局通知公告爬虫
    try:
        from Ministries import ndcpa_tzgg_crawler
        manager.register_crawler("国家疾控局_通知公告", ndcpa_tzgg_crawler.run, ndcpa_tzgg_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家疾控局通知公告爬虫失败: {e}")

    # 导入最高人民法院发布爬虫
    try:
        from Ministries import court_fabu_crawler
        manager.register_crawler("最高人民法院_发布", court_fabu_crawler.run, court_fabu_crawler)
    except ImportError as e:
        print(f"[WARN]  导入最高人民法院发布爬虫失败: {e}")

    # 导入最高人民检察院法规规范爬虫
    try:
        from Ministries import spp_flfh_crawler
        manager.register_crawler("最高人民检察院_法规规范", spp_flfh_crawler.run, spp_flfh_crawler)
    except ImportError as e:
        print(f"[WARN]  导入最高人民检察院法规规范爬虫失败: {e}")

    # 导入国家能源局最新文件爬虫
    try:
        from Ministries import nea_zxwj_crawler
        manager.register_crawler("国家能源局_最新文件", nea_zxwj_crawler.run, nea_zxwj_crawler)
    except ImportError as e:
        print(f"[WARN]  导入国家能源局最新文件爬虫失败: {e}")

    # 导入南京市工业和信息化局法定主动公开内容爬虫
    try:
        from City import nanjing_gxj_xxgk_crawler
        manager.register_crawler("南京市工业和信息化局_法定主动公开内容",nanjing_gxj_xxgk_crawler.run,nanjing_gxj_xxgk_crawler)
    except ImportError as e:
        print(f"[WARN]  导入南京市工业和信息化局_法定主动公开内容爬虫失败: {e}")
    
    try:
        manager.validate_crawler_selection()
    except ValueError as exc:
        print(f"[ERROR] 指定爬虫参数无效: {exc}")
        raise SystemExit(2)

    # 执行所有爬虫
    if manager.crawlers:
        results = manager.run_all_crawlers()

        # 打印执行摘要
        print("\n[SUMMARY] 执行摘要:")
        print("=" * 60)
        print(manager.get_summary())
    else:
        print("[WARN]  没有注册任何爬虫")
