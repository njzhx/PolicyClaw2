import os
import json
import requests
from supabase import create_client, Client
from datetime import datetime, timezone, timedelta

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
            return [], None

        try:
            processed_data = self.process_data(data_list, source_name)
            if not processed_data:
                print(f"⚠️  {source_name}：数据校验后没有可写入数据，跳过。")
                return [], {"status": "skipped", "message": "数据校验后没有可写入数据"}

            saved_items = []
            if self.allow_supabase_write:
                try:
                    # 尝试写入数据（不使用 on_conflict，避免约束错误）
                    supabase = self.get_client()
                    for item in processed_data:
                        try:
                            database_item = self.to_database_item(item)
                            # 优先按政策实体指纹幂等写入；数据库约束未就绪时退回标题匹配。
                            match_field = "policy_key"
                            try:
                                existing = (
                                    supabase.table(self.policy_table)
                                    .select("id")
                                    .eq("policy_key", database_item.get("policy_key"))
                                    .execute()
                                )
                            except Exception:
                                match_field = "title"
                                existing = (
                                    supabase.table(self.policy_table)
                                    .select("id")
                                    .eq("title", database_item.get("title"))
                                    .execute()
                                )

                            if existing.data:
                                # 已存在，更新数据
                                (
                                    supabase.table(self.policy_table)
                                    .update(database_item)
                                    .eq(match_field, database_item.get(match_field))
                                    .execute()
                                )
                            else:
                                # 不存在，插入数据
                                supabase.table(self.policy_table).insert(database_item).execute()

                            saved_items.append(item)

                        except Exception as item_e:
                            print(f"⚠️  {source_name}：单条数据处理失败 - {item_e}")
                            continue

                    print(f"✅ {source_name}：成功写入 {len(saved_items)} 条数据到 Supabase")
                except Exception as database_e:
                    print(f"❌ {source_name}：数据库写入失败 - {database_e}")
            else:
                print(
                    f"[DRY-RUN] {source_name}：Supabase 写入开关未开启，"
                    f"跳过写入 {len(processed_data)} 条数据。"
                    "设置 POLICYCLAW_ENABLE_SUPABASE_WRITE=1 后才会写入。"
                )

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

            # 返回抓取并标准化后的数据；数据库成功数由 Supabase 日志单独统计。
            return processed_data, api_push_result

        except Exception as e:
            print(f"❌ {source_name}：数据处理失败 - {e}")
            return [], None

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
