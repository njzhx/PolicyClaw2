import os
import json
import time
import requests
from supabase import create_client, Client
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from crawler_core import (
    api_push_enabled,
    dedupe_policy_items,
    normalize_policy_item,
    supabase_write_enabled,
    validate_policy_item,
)

# ==========================================
# 数据库工具模块
# 功能：提供统一的数据库操作功能，避免重复代码
# ==========================================

POLICY_TABLE_FIELDS = (
    "title",
    "url",
    "pub_at",
    "content",
    "selected",
    "category",
    "source",
    "policy_key",
)

UPSERT_BATCH_SIZE = 100
POLICYINTEL_MAX_BATCH_ITEMS = 100
POLICYINTEL_MAX_REQUEST_BYTES = 1536 * 1024
CRAWLER_RUN_TABLE = "crawler_run_records"

_storage_capture_active = False
_storage_capture_results = []


def _retry_after_seconds(response, default=5.0, maximum=60.0):
    """解析 429 Retry-After，限制等待时间以避免异常响应长期阻塞。"""
    raw_value = (response.headers.get("Retry-After") or "").strip()
    if not raw_value:
        return default
    try:
        seconds = float(raw_value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return default
    return min(max(seconds, 0.5), maximum)


def begin_storage_capture():
    """开始记录单个爬虫通过公共保存入口产生的 Supabase 结果。"""
    global _storage_capture_active
    _storage_capture_results.clear()
    _storage_capture_active = True


def consume_storage_results():
    """返回并清空当前爬虫产生的 Supabase 结果。"""
    global _storage_capture_active
    results = list(_storage_capture_results)
    _storage_capture_results.clear()
    _storage_capture_active = False
    return results


def _record_storage_result(source_name, storage_result, policyintel_result=None):
    if not _storage_capture_active:
        return
    entry = {"source_name": source_name}
    if isinstance(storage_result, dict):
        entry["storage_result"] = dict(storage_result)
    if isinstance(policyintel_result, dict):
        entry["policyintel_result"] = dict(policyintel_result)
    if len(entry) > 1:
        _storage_capture_results.append(entry)


def aggregate_storage_results(captured_results):
    """汇总一个爬虫内一次或多次 save_to_policy() 的真实写入统计。"""
    results = [
        entry.get("storage_result")
        for entry in (captured_results or [])
        if isinstance(entry, dict) and isinstance(entry.get("storage_result"), dict)
    ]
    if not results:
        return None
    if len(results) == 1:
        return dict(results[0])

    attempted = [
        result for result in results
        if result.get("status") in {"success", "partial", "error"}
    ]
    verified = [result for result in attempted if result.get("counts_verified") is True]
    inserted_count = sum(int(result.get("inserted_count") or 0) for result in verified)
    updated_count = sum(int(result.get("updated_count") or 0) for result in verified)
    skipped_count = sum(int(result.get("skipped_count") or 0) for result in verified)
    saved_count = sum(int(result.get("saved_count") or 0) for result in results)
    failed_count = sum(int(result.get("failed_count") or 0) for result in results)

    if attempted and all(result.get("status") == "error" for result in attempted):
        status = "error"
    elif any(result.get("status") in {"partial", "error"} for result in attempted):
        status = "partial"
    elif attempted:
        status = "success"
    else:
        status = "skipped"

    counts_verified = bool(attempted) and len(verified) == len(attempted)
    if counts_verified:
        message = f"Supabase 写入完成：新增 {inserted_count} 条"
        if skipped_count:
            message += f"，跳过已存在 {skipped_count} 条"
        elif updated_count:
            message += f"，更新 {updated_count} 条"
        if failed_count:
            message += f"，失败或无法核验 {failed_count} 条"
    elif status == "skipped" and all(
        result.get("message") == "没有数据需要写入" for result in results
    ):
        message = "没有数据需要写入"
    else:
        message = "部分保存结果未获得按 policy_key 核验的新增/更新统计"

    return {
        "status": status,
        "saved_count": saved_count,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "counts_verified": counts_verified,
        "message": message,
    }


def aggregate_policyintel_results(captured_results):
    """汇总一个爬虫内一次或多次 save_to_policy() 的 PolicyIntel 同步统计。"""
    results = [
        entry.get("policyintel_result")
        for entry in (captured_results or [])
        if isinstance(entry, dict)
        and isinstance(entry.get("policyintel_result"), dict)
    ]
    if not results:
        return None
    if len(results) == 1:
        return dict(results[0])

    attempted = [
        result for result in results
        if result.get("status") in {"success", "partial", "error"}
    ]
    inserted_count = sum(int(result.get("inserted_count") or 0) for result in attempted)
    skipped_count = sum(int(result.get("skipped_count") or 0) for result in attempted)
    invalid_count = sum(int(result.get("invalid_count") or 0) for result in attempted)
    failed_count = sum(int(result.get("failed_count") or 0) for result in attempted)
    total_received = sum(int(result.get("total_received") or 0) for result in attempted)
    retry_count = sum(int(result.get("retry_count") or 0) for result in attempted)
    errors = [
        str(error)
        for result in attempted
        for error in (result.get("errors") or [])
    ][:10]

    if attempted and all(result.get("status") == "error" for result in attempted):
        status = "error"
    elif any(result.get("status") in {"partial", "error"} for result in attempted):
        status = "partial"
    elif attempted:
        status = "success"
    else:
        status = "skipped"

    if status == "skipped":
        messages = [str(result.get("message") or "") for result in results]
        message = next((value for value in messages if value), "PolicyIntel 同步已跳过")
    else:
        message = (
            f"PolicyIntel 同步完成：新增 {inserted_count} 条，"
            f"跳过已存在 {skipped_count} 条"
        )
        if invalid_count:
            message += f"，无效 {invalid_count} 条"
        if failed_count:
            message += f"，失败 {failed_count} 条"
        if retry_count:
            message += f"，重试 {retry_count} 次"

    return {
        "status": status,
        "saved_count": inserted_count + skipped_count,
        "inserted_count": inserted_count,
        "skipped_count": skipped_count,
        "invalid_count": invalid_count,
        "failed_count": failed_count,
        "total_received": total_received,
        "counts_verified": bool(attempted),
        "retry_count": retry_count,
        "message": message,
        "errors": errors,
    }


class PolicySaveItems(list):
    """保留现有列表接口，同时携带结构化外部写入统计。"""

    def __init__(self, items=(), storage_result=None, policyintel_result=None):
        super().__init__(items)
        self.storage_result = storage_result
        self.policyintel_result = policyintel_result


def _save_return(
    source_name,
    items,
    storage_result,
    api_push_result,
    policyintel_result=None,
):
    _record_storage_result(source_name, storage_result, policyintel_result)
    return PolicySaveItems(
        items,
        storage_result,
        policyintel_result,
    ), api_push_result


class DBUtils:
    def __init__(self):
        """初始化数据库工具"""
        self.supabase_url = (
            os.environ.get("SUPABASE_PROJECT_URL")
            or os.environ.get("SUPABASE_PROJECT_API")
        )
        self.supabase_key = (
            os.environ.get("SUPABASE_SECRET_KEY")
            or os.environ.get("SUPABASE_ANON_PUBLIC")
        )
        self.policy_table = os.getenv("SUPABASE_TABLE", "policyclaw2").strip() or "policyclaw2"
        self.client = None
        self.allow_supabase_write = supabase_write_enabled()
        self.allow_api_push = api_push_enabled()

    def get_client(self) -> Client:
        """获取 Supabase 客户端

        Returns:
            Client: Supabase 客户端实例
        """
        if not self.client:
            if not self.supabase_url or not self.supabase_key:
                raise ValueError(
                    "缺少 Supabase 环境变量: SUPABASE_PROJECT_URL 或 SUPABASE_SECRET_KEY"
                )
            self.client = create_client(self.supabase_url, self.supabase_key)
        return self.client

    def save_crawler_run(self, record):
        """Persist one crawler health result independently from policy writes."""
        # 同步写入 PolicyIntel (VPS Postgres) 监控表
        try:
            policyintel_result = self.push_crawler_run_to_policyintel(record)
        except Exception as exc:
            policyintel_result = {
                "status": "error",
                "message": f"PolicyIntel 运行记录同步异常: {exc}",
                "attempts": 0,
                "status_code": None,
                "errors": [str(exc)],
            }

        def with_policyintel_result(supabase_result):
            """保持 Supabase 顶层返回约定，仅附加独立的 PolicyIntel 结果。"""
            return {
                **supabase_result,
                "policyintel_result": policyintel_result,
            }

        if not self.supabase_url or not self.supabase_key:
            return with_policyintel_result(
                {"status": "skipped", "message": "Supabase credentials are not configured"}
            )
        if os.getenv("POLICYCLAW_ENABLE_RUN_RECORDS", "1").strip().lower() in {
            "0", "false", "no", "off"
        }:
            return with_policyintel_result(
                {"status": "skipped", "message": "Crawler run recording is disabled"}
            )

        try:
            response = (
                self.get_client()
                .table(CRAWLER_RUN_TABLE)
                .upsert(record, on_conflict="run_id,crawler_key")
                .execute()
            )
            return with_policyintel_result(
                {"status": "success", "data": response.data}
            )
        except Exception as exc:
            return with_policyintel_result(
                {"status": "error", "message": str(exc)}
            )

    def process_data(self, data_list, source_name=""):
        """处理数据，准备写入数据库

        Args:
            data_list: 原始数据列表

        Returns:
            list: 处理后的数据列表
        """
        processed_data = []
        invalid_count = 0

        for item in data_list:
            processed_item = normalize_policy_item(item, source_name)
            missing = validate_policy_item(processed_item)
            if missing:
                invalid_count += 1
                print(f"⚠️  跳过核心字段缺失的数据：{','.join(missing)} - {processed_item.get('title') or processed_item.get('url')}")
                continue
            processed_data.append(processed_item)

        processed_data, duplicate_count = dedupe_policy_items(processed_data)
        if duplicate_count:
            print(f"⏭️  全局政策实体去重：跳过 {duplicate_count} 条重复数据")
        if invalid_count:
            print(f"⚠️  数据校验：跳过 {invalid_count} 条核心字段缺失数据")

        return processed_data

    @staticmethod
    def to_database_item(item):
        """只保留 policyclaw2 表实际存在并由爬虫负责写入的字段。"""
        return {field: item.get(field) for field in POLICY_TABLE_FIELDS if field in item}

    @staticmethod
    def iter_batches(items, batch_size=UPSERT_BATCH_SIZE):
        for index in range(0, len(items), batch_size):
            yield items[index:index + batch_size]

    @staticmethod
    def _policyintel_payload(source_name, items):
        return {
            "sources": [
                {
                    "name": source_name,
                    "category": (
                        items[0].get("category", "") if items else ""
                    ),
                    "items": items,
                }
            ]
        }

    @classmethod
    def _encode_policyintel_payload(cls, source_name, items):
        return json.dumps(
            cls._policyintel_payload(source_name, items),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def iter_policyintel_batches(cls, items, source_name):
        """按条数和实际 UTF-8 请求体大小切分 PolicyIntel 批次。"""
        batch = []
        for item in items:
            candidate = batch + [item]
            candidate_body = cls._encode_policyintel_payload(
                source_name,
                candidate,
            )
            if (
                len(candidate) <= POLICYINTEL_MAX_BATCH_ITEMS
                and len(candidate_body) <= POLICYINTEL_MAX_REQUEST_BYTES
            ):
                batch = candidate
                continue

            if batch:
                yield batch, cls._encode_policyintel_payload(
                    source_name,
                    batch,
                )
                batch = []

            single_body = cls._encode_policyintel_payload(
                source_name,
                [item],
            )
            if len(single_body) > POLICYINTEL_MAX_REQUEST_BYTES:
                yield None, {
                    "item": item,
                    "payload_bytes": len(single_body),
                }
            else:
                batch = [item]

        if batch:
            yield batch, cls._encode_policyintel_payload(
                source_name,
                batch,
            )

    def get_existing_policy_keys(self, supabase, policy_keys):
        """返回 Supabase 当前已存在的 policy_key 集合。"""
        keys = [key for key in policy_keys if key]
        if not keys:
            return set()
        response = (
            supabase.table(self.policy_table)
            .select("policy_key")
            .in_("policy_key", keys)
            .execute()
        )
        return {
            row.get("policy_key")
            for row in (response.data or [])
            if row.get("policy_key")
        }

    def save_to_policy(self, data_list, source_name):
        """保存数据到 policyclaw2 表

        Args:
            data_list: 数据列表
            source_name: 数据源名称

        Returns:
            tuple: (成功写入的数据列表, API推送结果)
        """
        if not data_list:
            print(f"⚠️  {source_name}：没有数据需要写入，跳过。")
            storage_result = {
                "status": "skipped",
                "saved_count": 0,
                "inserted_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "counts_verified": False,
                "message": "没有数据需要写入",
            }
            return _save_return(source_name, [], storage_result, None)

        try:
            processed_data = self.process_data(data_list, source_name)
            if not processed_data:
                print(f"⚠️  {source_name}：数据校验后没有可写入数据，跳过。")
                storage_result = {
                    "status": "skipped",
                    "saved_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "counts_verified": False,
                    "message": "数据校验后没有可写入数据",
                }
                return _save_return(source_name, [], storage_result, {
                    "status": "skipped",
                    "message": "数据校验后没有可写入数据",
                })

            saved_items = []
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            failed_count = 0
            storage_errors = []
            if self.allow_supabase_write:
                try:
                    # policy_key 需要数据库唯一约束；见 supabase_policy_key_unique.sql。
                    supabase = self.get_client()
                    for batch_items in self.iter_batches(processed_data):
                        try:
                            batch = [
                                self.to_database_item(item)
                                for item in batch_items
                            ]
                            batch_keys = [item["policy_key"] for item in batch]
                            keys_before = self.get_existing_policy_keys(
                                supabase, batch_keys
                            )
                            (
                                supabase.table(self.policy_table)
                                .upsert(
                                    batch,
                                    on_conflict="policy_key",
                                    ignore_duplicates=True,
                                )
                                .execute()
                            )
                            keys_after = self.get_existing_policy_keys(
                                supabase, batch_keys
                            )
                            persisted_keys = set(batch_keys) & keys_after
                            missing_keys = set(batch_keys) - persisted_keys
                            inserted_count += len(persisted_keys - keys_before)
                            skipped_count += len(persisted_keys & keys_before)
                            failed_count += len(missing_keys)
                            if missing_keys:
                                storage_errors.append(
                                    f"UPSERT 后有 {len(missing_keys)} 个 policy_key 无法核验"
                                )
                            saved_items.extend(
                                item
                                for item in batch_items
                                if item.get("policy_key") in persisted_keys
                            )

                        except Exception as batch_e:
                            failed_count += len(batch_items)
                            storage_errors.append(str(batch_e))
                            print(
                                f"⚠️  {source_name}：批量 UPSERT 失败，"
                                f"请确认 {self.policy_table}.policy_key 已创建唯一约束 - {batch_e}"
                            )
                            continue

                    print(f"✅ {source_name}：成功写入 {len(saved_items)} 条数据到 Supabase")
                except Exception as database_e:
                    failed_count = len(processed_data)
                    storage_errors.append(str(database_e))
                    print(f"❌ {source_name}：数据库写入失败 - {database_e}")
            else:
                print(
                    f"[DRY-RUN] {source_name}：Supabase 写入开关未开启，"
                    f"跳过写入 {len(processed_data)} 条数据。"
                    "设置 POLICYCLAW_ENABLE_SUPABASE_WRITE=1 后才会写入。"
                )

            if self.allow_supabase_write:
                if failed_count and saved_items:
                    storage_status = "partial"
                elif failed_count:
                    storage_status = "error"
                else:
                    storage_status = "success"
                storage_message = f"Supabase 写入完成：新增 {inserted_count} 条"
                if skipped_count:
                    storage_message += f"，跳过已存在 {skipped_count} 条"
                elif updated_count:
                    storage_message += f"，更新 {updated_count} 条"
                if failed_count:
                    storage_message += f"，失败或无法核验 {failed_count} 条"
                storage_result = {
                    "status": storage_status,
                    "saved_count": len(saved_items),
                    "inserted_count": inserted_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                    "failed_count": failed_count,
                    "counts_verified": True,
                    "message": storage_message,
                    "errors": storage_errors[:3],
                }
            else:
                storage_result = {
                    "status": "skipped",
                    "saved_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "counts_verified": False,
                    "message": "Supabase 写入开关未开启，未统计新增/更新",
                }

            # API 与 Supabase 独立：即使不写数据库，也可推送本次标准化后的数据。
            api_push_result = None
            if self.allow_api_push:
                api_push_result = self.push_to_api(processed_data, source_name)
            else:
                api_push_result = {
                    "status": "skipped",
                    "message": "API 推送开关未开启，跳过 push_to_api",
                }
                print(
                    f"[DRY-RUN] {source_name}：{api_push_result['message']}。"
                    "设置 POLICYCLAW_ENABLE_API_PUSH=1 后才会推送。"
                )

            # 独立推送到 PolicyIntel 新系统 (数据字段与 Supabase 保持 100% 严格一致)
            policyintel_result = None
            try:
                policyintel_result = self.push_to_policyintel(
                    processed_data,
                    source_name,
                )
            except Exception as pi_err:
                print(f"⚠️ PolicyIntel 推送异常 ({source_name}): {pi_err}")
                policyintel_result = {
                    "status": "error",
                    "saved_count": 0,
                    "inserted_count": 0,
                    "skipped_count": 0,
                    "invalid_count": 0,
                    "failed_count": len(processed_data),
                    "total_received": len(processed_data),
                    "counts_verified": False,
                    "message": f"PolicyIntel 推送异常: {pi_err}",
                    "errors": [str(pi_err)],
                }

            return _save_return(
                source_name,
                processed_data,
                storage_result,
                api_push_result,
                policyintel_result,
            )

        except Exception as e:
            print(f"❌ {source_name}：数据处理失败 - {e}")
            storage_result = {
                "status": "error",
                "saved_count": 0,
                "inserted_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "failed_count": len(data_list),
                "counts_verified": False,
                "message": f"数据处理失败 - {e}",
            }
            return _save_return(source_name, [], storage_result, None)

    def push_to_api(self, data_list, source_name):
        """将数据推送到目标API接口

        Args:
            data_list: 数据列表
            source_name: 数据源名称

        Returns:
            dict: 推送结果，包含status和message
        """
        if not data_list:
            print(f"⚠️  {source_name}：没有数据需要推送，跳过。")
            return {"status": "skipped", "message": "没有数据需要推送"}

        if not self.allow_api_push:
            message = (
                f"SKIP：API 推送开关未开启，未推送 {len(data_list)} 条数据。"
                "设置 POLICYCLAW_ENABLE_API_PUSH=1 后才会推送。"
            )
            print(f"[DRY-RUN] {source_name}：{message}")
            return {"status": "skipped", "message": message}

        vps_ip = os.getenv("VPS_IP", "").strip()
        if not vps_ip:
            message = "VPS_IP 环境变量未设置，跳过 API 推送"
            print(f"⚠️  {source_name}：{message}。")
            return {"status": "skipped", "message": message}

        target_url = f"http://{vps_ip}:5000/api/receive-data"

        try:
            # 构造JSON结构（按照接口示例格式）
            items = []
            for item in data_list:
                # 处理pub_at字段，确保是字符串格式
                pub_at = item.get('pub_at', '')
                if hasattr(pub_at, 'isoformat'):
                    pub_at = pub_at.isoformat()

                # 获取当前东八区时间作为crawled_at
                crawled_at = datetime.now(timezone(timedelta(hours=8))).isoformat()

                item_data = {
                    "title": item.get('title', ''),
                    "url": item.get('url', ''),
                    "content": item.get('content', ''),
                    "pub_at": pub_at,
                    "crawled_at": crawled_at
                }
                items.append(item_data)

            # 构建完整的JSON结构
            payload = {
                "sources": [
                    {
                        "name": source_name,
                        "items": items
                    }
                ]
            }

            # 发送POST请求
            headers = {"Content-Type": "application/json; charset=utf-8"}
            response = requests.post(
                target_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                timeout=10
            )

            # 检查响应状态
            response.raise_for_status()
            message = f"成功推送 {len(items)} 条数据到API"
            print(f"✅ {source_name}：{message}")
            return {"status": "success", "message": message}

        except requests.exceptions.RequestException as e:
            message = f"API推送失败 - {e}"
            print(f"❌ {source_name}：{message}")
            return {"status": "error", "message": message}
        except Exception as e:
            message = f"推送过程中发生未知错误 - {e}"
            print(f"❌ {source_name}：{message}")
            return {"status": "error", "message": message}

    def push_daily_status(self, date_str, success_count, fail_count):
        """推送每日爬虫状态数据到API接口

        Args:
            date_str: 日期字符串，格式为 YYYY-MM-DD
            success_count: 成功爬取的文章数
            fail_count: 失败的爬取数

        Returns:
            dict: 推送结果，包含status和message
        """
        try:
            # 使用东八区时间作为date
            # 如果没有提供date_str，则使用当前东八区日期
            if not date_str:
                east8_datetime = datetime.now(timezone(timedelta(hours=8)))
                east8_date = east8_datetime.date()
                date_str = east8_date.isoformat()

            # 构造payload
            payload = {
                "date": date_str,
                "success_count": success_count,
                "fail_count": fail_count
            }

            if not self.allow_api_push:
                message = (
                    f"DRY-RUN：模拟推送每日状态数据 - 日期={date_str}, "
                    f"成功={success_count}, 失败={fail_count}，API 推送开关未开启"
                )
                print(f"[DRY-RUN] {message}")
                return {"status": "dry_run", "message": message, "payload": payload}

            vps_ip = os.getenv("VPS_IP", "").strip()
            if not vps_ip:
                message = "VPS_IP 环境变量未设置，跳过每日状态推送"
                print(f"⚠️  {message}。")
                return {"status": "skipped", "message": message}

            target_url = f"http://{vps_ip}:5000/api/receive-daily-status"

            # 发送POST请求
            headers = {"Content-Type": "application/json; charset=utf-8"}
            response = requests.post(
                target_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                timeout=10
            )

            # 检查响应状态
            response.raise_for_status()
            message = f"成功推送每日状态数据 - 日期={date_str}, 成功={success_count}, 失败={fail_count}"
            print(f"✅ {message}")
            return {"status": "success", "message": message}

        except requests.exceptions.RequestException as e:
            message = f"每日状态数据推送失败 - {e}"
            print(f"❌ {message}")
            return {"status": "error", "message": message}
        except Exception as e:
            message = f"推送过程中发生未知错误 - {e}"
            print(f"❌ {message}")
            return {"status": "error", "message": message}

    def push_to_policyintel(self, data_list, source_name):
        """将数据同步推送到 PolicyIntel (VPS Postgres) 系统，数据字段与 Supabase 保持 100% 严格一致"""
        if not data_list:
            return {"status": "skipped", "message": "没有数据需要推送"}

        if os.getenv("POLICYINTEL_SYNC_ENABLED", "0").strip().lower() in {"0", "false", "no", "off"}:
            return {"status": "skipped", "message": "PolicyIntel 同步开关未开启"}

        api_base = os.getenv("POLICYINTEL_API_BASE_URL", "").strip().rstrip("/")
        if not api_base:
            vps_ip = os.getenv("VPS_IP", "").strip()
            if vps_ip:
                # 默认走 Nginx 5173 代理端口或直接配置的端口
                vps_port = os.getenv("POLICYINTEL_PORT", "5173").strip()
                api_base = f"http://{vps_ip}:{vps_port}/api" if vps_port else f"http://{vps_ip}/api"

        if not api_base:
            return {"status": "skipped", "message": "未配置 POLICYINTEL_API_BASE_URL 或 VPS_IP，跳过 PolicyIntel 同步"}

        target_url = f"{api_base}/receive-data"
        api_key = os.getenv("CRAWLER_API_KEY", "").strip()
        if not api_key:
            message = "未配置 CRAWLER_API_KEY，跳过 PolicyIntel 同步"
            print(f"⚠️  [PolicyIntel Sync] {message}")
            return {"status": "skipped", "message": message}

        items = []
        for item in data_list:
            # 严格调用 to_database_item，确保字段与写入 Supabase 的完全一致。
            db_item = self.to_database_item(item)
            items.append({
                "title": db_item.get("title", ""),
                "url": db_item.get("url", ""),
                "pub_at": db_item.get("pub_at", ""),
                "content": db_item.get("content", ""),
                "selected": bool(db_item.get("selected", False)),
                "category": db_item.get("category", ""),
                "source": db_item.get("source", "") or source_name,
                "policy_key": db_item.get("policy_key", ""),
            })

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-api-key": api_key,
        }
        inserted_count = 0
        skipped_count = 0
        invalid_count = 0
        failed_count = 0
        errors = []
        counts_verified = True
        received_non_success = False
        retry_count = 0
        max_attempts = 3
        transient_statuses = {408, 425, 429}

        for batch_index, (batch_items, batch_body) in enumerate(
            self.iter_policyintel_batches(items, source_name),
            start=1,
        ):
            if batch_items is None:
                failed_count += 1
                received_non_success = True
                oversized_item = batch_body["item"]
                errors.append(
                    "单条政策请求体超过 PolicyIntel 1.5 MiB 安全上限，"
                    "未截断正文且未发送: "
                    f"{oversized_item.get('title') or oversized_item.get('policy_key')} "
                    f"({batch_body['payload_bytes']} bytes)"
                )
                continue
            batch_result = None
            request_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.post(
                        target_url,
                        data=batch_body,
                        headers=headers,
                        timeout=15,
                        proxies=(
                            {"http": None, "https": None}
                            if os.name == "nt" else None
                        ),
                    )
                except requests.exceptions.RequestException as exc:
                    request_error = str(exc)
                    if attempt < max_attempts:
                        retry_count += 1
                        time.sleep(0.5 * attempt)
                        continue
                    break
                except Exception as exc:
                    request_error = str(exc)
                    break

                status_code = response.status_code
                if not 200 <= status_code <= 299:
                    detail = (response.text or "").strip()[:300]
                    request_error = f"HTTP {status_code}"
                    if detail:
                        request_error += f": {detail}"
                    is_transient = (
                        status_code in transient_statuses
                        or 500 <= status_code <= 599
                    )
                    if is_transient and attempt < max_attempts:
                        retry_count += 1
                        retry_delay = (
                            _retry_after_seconds(response)
                            if status_code == 429
                            else 0.5 * attempt
                        )
                        time.sleep(retry_delay)
                        continue
                    break

                try:
                    batch_result = response.json()
                except ValueError as exc:
                    request_error = f"响应不是有效 JSON: {exc}"
                break

            if batch_result is None:
                counts_verified = False
                received_non_success = True
                failed_count += len(batch_items)
                errors.append(
                    f"批次 {batch_index} 请求失败: "
                    f"{request_error or '未知错误'}"
                )
                continue

            try:
                batch_status = batch_result.get("status") or "unknown"
                if batch_status in {"partial", "error"}:
                    received_non_success = True
                batch_inserted = int(batch_result.get("inserted_count") or 0)
                batch_skipped = int(batch_result.get("skipped_count") or 0)
                batch_invalid = int(batch_result.get("invalid_count") or 0)
                batch_failed = int(batch_result.get("failed_count") or 0)
                accounted_count = (
                    batch_inserted
                    + batch_skipped
                    + batch_invalid
                    + batch_failed
                )
                if accounted_count != len(batch_items):
                    counts_verified = False
                    received_non_success = True
                    if accounted_count < len(batch_items):
                        batch_failed += len(batch_items) - accounted_count
                    errors.append(
                        f"批次 {batch_index} 计数不一致: "
                        f"发送 {len(batch_items)} 条，服务端统计 {accounted_count} 条"
                    )
                inserted_count += batch_inserted
                skipped_count += batch_skipped
                invalid_count += batch_invalid
                failed_count += batch_failed
                errors.extend(
                    f"批次 {batch_index}: {error}"
                    for error in (batch_result.get("errors") or [])
                )
            except Exception as exc:
                counts_verified = False
                received_non_success = True
                failed_count += len(batch_items)
                errors.append(f"批次 {batch_index} 请求失败: {exc}")

        saved_count = inserted_count + skipped_count
        if received_non_success or failed_count or invalid_count:
            status = "partial" if saved_count else "error"
        else:
            status = "success"

        message = (
            f"PolicyIntel 同步完成：新增 {inserted_count} 条，"
            f"跳过已存在 {skipped_count} 条"
        )
        if invalid_count:
            message += f"，无效 {invalid_count} 条"
        if failed_count:
            message += f"，失败 {failed_count} 条"
        if retry_count:
            message += f"，重试 {retry_count} 次"

        result = {
            "status": status,
            "saved_count": saved_count,
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "invalid_count": invalid_count,
            "failed_count": failed_count,
            "total_received": len(items),
            "counts_verified": counts_verified,
            "retry_count": retry_count,
            "message": message,
            "errors": errors[:10],
        }
        if status == "success":
            print(f"[PolicyIntel Sync OK] {source_name}: {message}")
        else:
            print(f"[PolicyIntel Sync {status.upper()}] {source_name}: {message}")
        return result

    def push_crawler_run_to_policyintel(self, record):
        """同步推送单次爬虫运行记录到 PolicyIntel API"""
        if os.getenv("POLICYINTEL_SYNC_ENABLED", "0").strip().lower() in {"0", "false", "no", "off"}:
            return {
                "status": "skipped",
                "message": "PolicyIntel 同步开关未开启",
                "attempts": 0,
                "status_code": None,
                "errors": [],
            }
        api_base = os.getenv("POLICYINTEL_API_BASE_URL", "").strip().rstrip("/")
        if not api_base:
            vps_ip = os.getenv("VPS_IP", "").strip()
            if vps_ip:
                vps_port = os.getenv("POLICYINTEL_PORT", "5173").strip()
                api_base = f"http://{vps_ip}:{vps_port}/api" if vps_port else f"http://{vps_ip}/api"
        if not api_base:
            return {
                "status": "skipped",
                "message": "未配置 POLICYINTEL_API_BASE_URL 或 VPS_IP",
                "attempts": 0,
                "status_code": None,
                "errors": [],
            }

        target_url = f"{api_base}/crawler/runs"
        api_key = os.getenv("CRAWLER_API_KEY", "").strip()
        if not api_key:
            return {
                "status": "skipped",
                "message": "未配置 CRAWLER_API_KEY",
                "attempts": 0,
                "status_code": None,
                "errors": [],
            }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-api-key": api_key,
        }
        max_attempts = 3
        transient_statuses = {408, 425, 429}
        errors = []

        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    target_url,
                    json=record,
                    headers=headers,
                    timeout=10,
                    proxies={"http": None, "https": None} if os.name == "nt" else None,
                )
                status_code = response.status_code
                is_transient = (
                    status_code in transient_statuses
                    or 500 <= status_code <= 599
                )
                if not 200 <= status_code <= 299:
                    detail = (response.text or "").strip()[:300]
                    error = f"HTTP {status_code}"
                    if detail:
                        error += f": {detail}"
                    errors.append(error)
                    if is_transient and attempt < max_attempts:
                        retry_delay = (
                            _retry_after_seconds(response)
                            if status_code == 429
                            else 0.5 * attempt
                        )
                        time.sleep(retry_delay)
                        continue
                    result = {
                        "status": "error",
                        "message": f"PolicyIntel 运行记录同步失败: {error}",
                        "attempts": attempt,
                        "status_code": status_code,
                        "errors": errors,
                    }
                    print(f"[PolicyIntel Run Sync ERROR] {result['message']}")
                    return result

                try:
                    response_data = response.json()
                except ValueError as exc:
                    error = f"响应不是有效 JSON: {exc}"
                    result = {
                        "status": "error",
                        "message": f"PolicyIntel 运行记录同步失败: {error}",
                        "attempts": attempt,
                        "status_code": status_code,
                        "errors": errors + [error],
                    }
                    print(f"[PolicyIntel Run Sync ERROR] {result['message']}")
                    return result

                if response_data.get("success") is not True:
                    error = str(
                        response_data.get("message")
                        or "服务端未确认写入成功"
                    )
                    result = {
                        "status": "error",
                        "message": f"PolicyIntel 运行记录同步失败: {error}",
                        "attempts": attempt,
                        "status_code": status_code,
                        "errors": errors + [error],
                    }
                    print(f"[PolicyIntel Run Sync ERROR] {result['message']}")
                    return result

                result = {
                    "status": "success",
                    "message": str(
                        response_data.get("message")
                        or "PolicyIntel 运行记录同步成功"
                    ),
                    "attempts": attempt,
                    "status_code": status_code,
                    "errors": errors,
                }
                print(f"[PolicyIntel Run Sync OK] {result['message']}")
                return result
            except requests.exceptions.RequestException as exc:
                error = str(exc)
                errors.append(error)
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)
                    continue
                result = {
                    "status": "error",
                    "message": f"PolicyIntel 运行记录同步失败: {error}",
                    "attempts": attempt,
                    "status_code": None,
                    "errors": errors,
                }
                print(f"[PolicyIntel Run Sync ERROR] {result['message']}")
                return result

# 创建全局实例
db_utils = DBUtils()

# 便捷函数
def save_to_policy(data_list, source_name):
    """便捷函数：保存数据到 policy 表

    Args:
        data_list: 数据列表
        source_name: 数据源名称

    Returns:
        tuple: (成功写入的数据列表, API推送结果)
    """
    return db_utils.save_to_policy(data_list, source_name)

# 便捷函数
def push_to_api(data_list, source_name):
    """便捷函数：将数据推送到API接口

    Args:
        data_list: 数据列表
        source_name: 数据源名称

    Returns:
        bool: 是否成功推送
    """
    return db_utils.push_to_api(data_list, source_name)

# 便捷函数
def push_daily_status(date_str, success_count, fail_count):
    """便捷函数：推送每日爬虫状态数据到API接口

    Args:
        date_str: 日期字符串，格式为 YYYY-MM-DD
        success_count: 成功爬取的文章数
        fail_count: 失败的爬取数

    Returns:
        bool: 是否成功推送
    """
    return db_utils.push_daily_status(date_str, success_count, fail_count)


def save_crawler_run(record):
    """Convenience wrapper for writing one crawler execution record."""
    return db_utils.save_crawler_run(record)
