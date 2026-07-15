"""对 PolicyClaw 使用的 Supabase 表执行一次安全的 CRUD 连通性测试。

密钥只从环境变量读取，绝不能写入本文件。测试数据带有唯一 policy_key，
测试结束或中途失败时会尽力删除该条测试数据。
"""

import os
import sys
import uuid
from getpass import getpass
from datetime import datetime, timezone

import requests
from supabase import Client, create_client


PROJECT_URL_ENV = "SUPABASE_PROJECT_URL"
SECRET_KEY_ENV = "SUPABASE_SECRET_KEY"
TABLE_ENV = "SUPABASE_TABLE"
DEFAULT_TABLE = "policyclaw2"


class CrudTestError(RuntimeError):
    """CRUD 测试结果不符合预期。"""


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise CrudTestError(f"缺少环境变量 {name}")
    return value


def get_secret_key() -> str:
    value = os.getenv(SECRET_KEY_ENV, "").strip()
    if value:
        return value
    if not sys.stdin.isatty():
        raise CrudTestError(
            f"缺少环境变量 {SECRET_KEY_ENV}，且当前终端不支持安全交互输入"
        )
    value = getpass("Supabase secret key（输入内容不会显示）: ").strip()
    if not value:
        raise CrudTestError("Supabase secret key 不能为空")
    return value


def mask_project_url(project_url: str) -> str:
    if ".supabase.co" not in project_url:
        return "<已隐藏的 Supabase URL>"
    project_ref = project_url.split("//", 1)[-1].split(".", 1)[0]
    if len(project_ref) <= 6:
        return "https://***.supabase.co"
    return f"https://{project_ref[:3]}***{project_ref[-3:]}.supabase.co"


def build_test_item(test_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "title": f"[PolicyClaw CRUD Test] {test_id}",
        "url": f"https://example.invalid/policyclaw-crud-test/{test_id}",
        "pub_at": now.date().isoformat(),
        "content": "Supabase CRUD connection test: created",
        "source": "PolicyClaw CRUD Test",
        "category": "中央部委",
        "doc_no": "",
        "issuer": "PolicyClaw",
        "attachments": [],
        "crawled_at": now.isoformat(),
        "selected": False,
        "policy_key": f"crud-test-{test_id}",
    }


def get_table_columns(project_url: str, secret_key: str, table_name: str) -> set[str]:
    response = requests.get(
        f"{project_url.rstrip('/')}/rest/v1/",
        headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        timeout=20,
    )
    response.raise_for_status()
    definition = (response.json().get("definitions") or {}).get(table_name)
    if not definition:
        raise CrudTestError(f"Supabase OpenAPI schema 中未找到数据表 {table_name}")
    return set((definition.get("properties") or {}).keys())


def assert_single_row(rows, match_column: str, match_value: str, action: str) -> dict:
    matched = [row for row in (rows or []) if row.get(match_column) == match_value]
    if len(matched) != 1:
        raise CrudTestError(f"{action}校验失败：期望 1 条记录，实际得到 {len(matched)} 条")
    return matched[0]


def cleanup_test_row(
    client: Client,
    table_name: str,
    match_column: str,
    match_value: str,
) -> None:
    client.table(table_name).delete().eq(match_column, match_value).execute()


def run_crud_test(project_url: str, secret_key: str, table_name: str) -> None:
    test_id = uuid.uuid4().hex
    test_item = build_test_item(test_id)
    inserted = False

    print(f"目标项目: {mask_project_url(project_url)}")
    print(f"目标数据表: {table_name}")

    client = create_client(project_url, secret_key)

    try:
        client.table(table_name).select("*").limit(1).execute()
        print("[PASS] CONNECT/SELECT：项目连接及数据表读取正常")

        table_columns = get_table_columns(project_url, secret_key, table_name)
        writable_item = {key: value for key, value in test_item.items() if key in table_columns}
        omitted_columns = sorted(set(test_item) - table_columns)
        if omitted_columns:
            print(f"[INFO] 数据表不存在以下标准字段，本次测试已跳过：{', '.join(omitted_columns)}")

        match_column = next(
            (column for column in ("policy_key", "url", "title") if column in writable_item),
            None,
        )
        if not match_column:
            raise CrudTestError("数据表缺少 policy_key、url、title，无法安全定位和清理测试记录")
        match_value = writable_item[match_column]

        client.table(table_name).insert(writable_item).execute()
        inserted = True
        print("[PASS] CREATE：测试记录新增成功")

        read_response = (
            client.table(table_name)
            .select("*")
            .eq(match_column, match_value)
            .execute()
        )
        created_row = assert_single_row(read_response.data, match_column, match_value, "READ")
        if created_row.get("title") != test_item["title"]:
            raise CrudTestError("READ 校验失败：读取到的 title 与写入值不一致")
        print(f"[PASS] READ：按 {match_column} 查询测试记录成功")

        updated_content = "Supabase CRUD connection test: updated"
        update_values = {}
        if "selected" in table_columns:
            update_values["selected"] = True
        if "content" in table_columns:
            update_values["content"] = updated_content
        if not update_values:
            raise CrudTestError("数据表缺少 selected/content 字段，无法执行安全的 UPDATE 测试")
        client.table(table_name).update(update_values).eq(match_column, match_value).execute()

        update_response = (
            client.table(table_name)
            .select("*")
            .eq(match_column, match_value)
            .execute()
        )
        updated_row = assert_single_row(update_response.data, match_column, match_value, "UPDATE")
        for key, value in update_values.items():
            if updated_row.get(key) != value:
                raise CrudTestError(f"UPDATE 校验失败：字段 {key} 没有按预期更新")
        print("[PASS] UPDATE：测试记录更新成功")

        cleanup_test_row(client, table_name, match_column, match_value)
        inserted = False

        delete_response = (
            client.table(table_name)
            .select(match_column)
            .eq(match_column, match_value)
            .execute()
        )
        if delete_response.data:
            raise CrudTestError("DELETE 校验失败：删除后仍能查询到测试记录")
        print("[PASS] DELETE：测试记录删除成功")
        print("\nSupabase CRUD 测试全部通过。")
    finally:
        if inserted:
            try:
                cleanup_test_row(client, table_name, match_column, match_value)
                print("[CLEANUP] 已清理中途失败时遗留的测试记录")
            except Exception as cleanup_error:
                print(
                    f"[WARN] 自动清理失败，请在 {table_name} 表中手动删除 "
                    f"{match_column}={match_value} 的记录：{cleanup_error}",
                    file=sys.stderr,
                )


def main() -> int:
    try:
        project_url = require_env(PROJECT_URL_ENV)
        secret_key = get_secret_key()
        table_name = os.getenv(TABLE_ENV, DEFAULT_TABLE).strip() or DEFAULT_TABLE
        run_crud_test(project_url, secret_key, table_name)
        return 0
    except Exception as error:
        print(f"\n[FAIL] Supabase CRUD 测试失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
