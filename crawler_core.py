import hashlib
import os
import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


STANDARD_FIELDS = (
    "title",
    "url",
    "pub_at",
    "content",
    "source",
    "category",
    "doc_no",
    "issuer",
    "attachments",
    "crawled_at",
)

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "from",
    "spm",
    "source",
    "timestamp",
    "_",
}


@dataclass
class CrawlerMetrics:
    raw_item_count: int = 0
    valid_item_count: int = 0
    target_date_count: int = 0
    filtered_count: int = 0
    invalid_item_count: int = 0
    empty_content_count: int = 0
    duplicate_policy_count: int = 0
    saved_count: int = 0
    api_push_failed_count: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_item_count": self.raw_item_count,
            "valid_item_count": self.valid_item_count,
            "target_date_count": self.target_date_count,
            "filtered_count": self.filtered_count,
            "invalid_item_count": self.invalid_item_count,
            "empty_content_count": self.empty_content_count,
            "duplicate_policy_count": self.duplicate_policy_count,
            "saved_count": self.saved_count,
            "api_push_failed_count": self.api_push_failed_count,
            "errors": self.errors,
        }


@dataclass
class CrawlerRunResult:
    """单站爬虫的统一结构化返回结果。"""

    items: List[Dict[str, Any]] = field(default_factory=list)
    latest_items: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Any = field(default_factory=CrawlerMetrics)
    storage_result: Optional[Dict[str, Any]] = None
    api_push_result: Optional[Dict[str, Any]] = None


def beijing_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def env_flag_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def external_send_enabled() -> bool:
    """兼容旧配置中的总外部发送开关。"""
    return env_flag_enabled("POLICYCLAW_ENABLE_EXTERNAL_SEND")


def supabase_write_enabled() -> bool:
    """Supabase 独立写入开关；未配置时兼容旧总开关。"""
    legacy_default = external_send_enabled()
    return env_flag_enabled("POLICYCLAW_ENABLE_SUPABASE_WRITE", legacy_default)


def api_push_enabled() -> bool:
    """业务数据 API 和每日状态 API 的独立推送开关。"""
    return env_flag_enabled("POLICYCLAW_ENABLE_API_PUSH")


def feishu_notify_enabled() -> bool:
    """飞书结果提醒独立开关；未配置时兼容旧总开关。"""
    legacy_default = external_send_enabled()
    return env_flag_enabled("POLICYCLAW_ENABLE_FEISHU_NOTIFY", legacy_default)


def get_crawl_date_window(today: Optional[date] = None) -> Tuple[date, date]:
    today = today or beijing_now().date()
    mode = os.getenv("CRAWL_MODE", "").strip().lower()
    single_date = os.getenv("CRAWL_DATE", "").strip()
    date_from = os.getenv("CRAWL_DATE_FROM", "").strip()
    date_to = os.getenv("CRAWL_DATE_TO", "").strip()
    window_days = os.getenv("CRAWL_WINDOW_DAYS", "").strip()

    valid_modes = {"sliding_window", "single_date", "date_range"}
    if mode and mode not in valid_modes:
        raise ValueError(
            "CRAWL_MODE 只能是 sliding_window、single_date 或 date_range"
        )

    # 兼容本地直接设置旧环境变量的方式，同时禁止互相冲突的日期参数。
    if not mode:
        if single_date:
            mode = "single_date"
        elif date_from or date_to:
            mode = "date_range"
        else:
            mode = "sliding_window"

    if mode == "single_date":
        if not single_date:
            raise ValueError("single_date 模式必须填写 CRAWL_DATE")
        if date_from or date_to or window_days:
            raise ValueError(
                "single_date 模式不能同时设置日期范围或滑动窗口天数"
            )
        parsed = parse_date(single_date)
        if not parsed:
            raise ValueError("CRAWL_DATE 必须是 YYYY-MM-DD 格式")
        return parsed, parsed

    if mode == "date_range":
        if not date_from or not date_to:
            raise ValueError(
                "date_range 模式必须同时填写 CRAWL_DATE_FROM 和 CRAWL_DATE_TO"
            )
        if single_date or window_days:
            raise ValueError(
                "date_range 模式不能同时设置单日日期或滑动窗口天数"
            )
        start = parse_date(date_from)
        end = parse_date(date_to)
        if not start or not end:
            raise ValueError("CRAWL_DATE_FROM/CRAWL_DATE_TO 必须是 YYYY-MM-DD 格式")
        if start > end:
            raise ValueError("CRAWL_DATE_FROM 不能晚于 CRAWL_DATE_TO")
        return start, end

    if single_date or date_from or date_to:
        raise ValueError("sliding_window 模式不能同时设置单日或日期范围")
    try:
        days = int(window_days or "7")
    except ValueError as exc:
        raise ValueError("CRAWL_WINDOW_DAYS 必须是正整数") from exc
    if days < 1:
        raise ValueError("CRAWL_WINDOW_DAYS 必须是正整数")
    return today - timedelta(days=days), today - timedelta(days=1)


def format_date_window(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return start_date.isoformat()
    return f"{start_date.isoformat()} 至 {end_date.isoformat()}"


def is_target_date(value: Any, start_date: date, end_date: date) -> bool:
    parsed = parse_date(value)
    if not parsed:
        return False
    return start_date <= parsed <= end_date


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text[:10] if fmt != "%Y年%m月%d日" else text, fmt).date()
        except ValueError:
            continue

    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def normalize_title(title: Any) -> str:
    text = "" if title is None else str(title)
    text = re.sub(r"\s+", "", text)
    return text.strip("　 \t\r\n")


def normalize_url(url: Any, base_url: str = "") -> str:
    text = "" if url is None else str(url).strip()
    if not text:
        return ""
    absolute = urljoin(base_url, text)
    parts = urlsplit(absolute)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS:
            continue
        if any(lower_key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))

    return urlunsplit(
        (
            scheme,
            netloc,
            parts.path or "/",
            urlencode(query_items, doseq=True),
            "",
        )
    )


def normalize_policy_item(item: Dict[str, Any], source_name: str = "", base_url: str = "") -> Dict[str, Any]:
    normalized = dict(item)
    normalized["title"] = str(normalized.get("title") or "").strip()
    normalized["url"] = normalize_url(normalized.get("url"), base_url)
    pub_at = parse_date(normalized.get("pub_at"))
    normalized["pub_at"] = pub_at.isoformat() if pub_at else ""
    normalized["content"] = str(normalized.get("content") or "").strip()
    normalized["source"] = normalized.get("source") or source_name
    normalized["category"] = normalized.get("category") or ""
    normalized["doc_no"] = normalized.get("doc_no") or ""
    normalized["issuer"] = normalized.get("issuer") or ""
    normalized["attachments"] = normalized.get("attachments") or []
    normalized["crawled_at"] = normalized.get("crawled_at") or beijing_now().isoformat()
    normalized.setdefault("selected", False)
    normalized["policy_key"] = policy_identity_key(normalized)
    return normalized


def validate_policy_item(item: Dict[str, Any]) -> List[str]:
    missing = []
    for field_name in ("title", "url", "pub_at"):
        if not item.get(field_name):
            missing.append(field_name)
    return missing


def policy_identity_key(item: Dict[str, Any]) -> str:
    title = normalize_title(item.get("title"))
    doc_no = normalize_title(item.get("doc_no"))
    pub_at = parse_date(item.get("pub_at"))
    pub_text = pub_at.isoformat() if pub_at else str(item.get("pub_at") or "").strip()
    if doc_no:
        raw_key = f"{title}|{doc_no}|{pub_text}"
    else:
        raw_key = f"{title}|{pub_text}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def dedupe_policy_items(items: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    seen = set()
    deduped = []
    duplicates = 0
    for item in items:
        key = item.get("policy_key") or policy_identity_key(item)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, duplicates


def extract_metrics_from_output(output: str, item_count: int = 0) -> CrawlerMetrics:
    metrics = CrawlerMetrics()
    target_match = re.search(
        r"成功抓取\s*(?:(\d+)\s*条目标日期|目标日期(?:窗口)?数据\s*[:：]\s*(\d+)\s*条)",
        output,
    )
    metrics.target_date_count = (
        int(next(group for group in target_match.groups() if group is not None))
        if target_match
        else item_count
    )
    metrics.valid_item_count = item_count

    filter_match = re.search(
        r"(?:过滤掉|过滤非(?:昨日|目标日期)(?:窗口)?(?:/无效)?数据)\s*[:：]?\s*(\d+)\s*条",
        output,
    )
    if filter_match:
        metrics.filtered_count = int(filter_match.group(1))

    latest_count = len(re.findall(r"^✅\s+.+?\s+\d{4}-\d{2}-\d{2}\s*$", output, re.MULTILINE))
    metrics.raw_item_count = max(
        metrics.target_date_count + metrics.filtered_count,
        latest_count,
        item_count,
    )
    if metrics.raw_item_count:
        metrics.valid_item_count = max(metrics.valid_item_count, metrics.raw_item_count - metrics.invalid_item_count)

    if item_count == 0 and metrics.filtered_count == 0:
        metrics.errors.append("filtered_count 和 target_date_count 均为 0，疑似列表未解析到有效数据")
    return metrics


def extract_latest_items_from_output(output: str, limit: int = 5) -> List[Dict[str, str]]:
    """兼容旧爬虫：从“页面最新5条”日志中提取标题和发布日期。"""
    latest_items = []
    in_latest_section = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "最新5条" in line:
            in_latest_section = True
            continue
        if not in_latest_section:
            continue
        if not line:
            continue
        if "写入数据库" in line or "Supabase" in line or "推送" in line or set(line) == {"-"}:
            break

        date_match = re.search(r"(\d{4}-\d{1,2}-\d{1,2}|未知日期)\s*$", line)
        if not date_match:
            continue

        date_text = date_match.group(1)
        title = line[:date_match.start()].strip()
        title = re.sub(r"^(?:✅|\[OK\]|\[成功\])\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(r"^(?:\d+[.、]|第\d+条[:：])\s*", "", title)
        title = re.sub(r"\.{3,}$", "", title).strip()
        if not title:
            continue

        parsed_date = parse_date(date_text)
        latest_items.append(
            {
                "title": title,
                "pub_at": parsed_date.isoformat() if parsed_date else "",
            }
        )
        if len(latest_items) >= limit:
            break

    return latest_items


def extract_storage_result_from_output(output: str, item_count: int = 0) -> Dict[str, Any]:
    """兼容旧数据库接口：把保存日志转换为结构化状态。"""
    success_match = re.search(r"成功写入\s*(\d+)\s*条数据到\s*Supabase", output, re.IGNORECASE)
    if success_match:
        saved_count = int(success_match.group(1))
        return {
            "status": "success",
            "saved_count": saved_count,
            "message": f"成功写入 {saved_count} 条数据到 Supabase",
        }

    if "Supabase 写入开关未开启" in output:
        return {
            "status": "skipped",
            "saved_count": 0,
            "message": "Supabase 写入开关未开启，已跳过数据库写入",
        }
    if "数据库写入失败" in output:
        return {"status": "error", "saved_count": 0, "message": "数据库写入失败"}
    if "没有数据需要写入" in output or "没有可写入数据" in output:
        return {"status": "skipped", "saved_count": 0, "message": "没有数据需要写入"}

    if "[DRY-RUN]" in output:
        return {
            "status": "dry_run",
            "saved_count": item_count,
            "message": f"DRY-RUN：模拟写入 {item_count} 条数据到 Supabase",
        }
    if "数据库写入失败" in output:
        return {"status": "error", "saved_count": 0, "message": "数据库写入失败"}
    if "没有数据需要写入" in output or "没有可写入数据" in output:
        return {"status": "skipped", "saved_count": 0, "message": "没有数据需要写入"}
    return {
        "status": "unknown",
        "saved_count": item_count,
        "message": "未获得 Supabase 写入状态",
    }


def adapt_legacy_result(result: Any, output: str, source_name: str = "") -> Dict[str, Any]:
    api_push_result = None
    storage_result = None
    latest_items = []
    data_list = []

    if isinstance(result, CrawlerRunResult):
        items = result.items or []
        latest_items = result.latest_items or []
        metric_fields = {field_info.name for field_info in fields(CrawlerMetrics)}
        metric_values = result.metrics.to_dict() if isinstance(result.metrics, CrawlerMetrics) else (result.metrics or {})
        metrics = CrawlerMetrics(**{k: v for k, v in metric_values.items() if k in metric_fields})
        storage_result = result.storage_result
        api_push_result = result.api_push_result
    elif isinstance(result, dict) and "items" in result:
        items = result.get("items") or []
        latest_items = result.get("latest_items") or []
        metric_fields = {field_info.name for field_info in fields(CrawlerMetrics)}
        metrics = CrawlerMetrics(
            **{k: v for k, v in (result.get("metrics") or {}).items() if k in metric_fields}
        )
        storage_result = result.get("storage_result")
        api_push_result = result.get("api_push_result")
    elif isinstance(result, tuple) and len(result) == 2:
        data_list, api_push_result = result
        items = data_list or []
        metrics = extract_metrics_from_output(output, len(items))
    else:
        items = result or []
        metrics = extract_metrics_from_output(output, len(items))

    if (
        output
        and metrics.raw_item_count == 0
        and metrics.target_date_count == 0
        and metrics.filtered_count == 0
    ):
        extracted_metrics = extract_metrics_from_output(output, len(items))
        metrics.raw_item_count = extracted_metrics.raw_item_count
        metrics.valid_item_count = extracted_metrics.valid_item_count
        metrics.target_date_count = extracted_metrics.target_date_count
        metrics.filtered_count = extracted_metrics.filtered_count
        metrics.errors.extend(extracted_metrics.errors)

    normalized_items = []
    for item in items:
        if isinstance(item, dict):
            normalized = normalize_policy_item(item, source_name)
            missing = validate_policy_item(normalized)
            if missing:
                metrics.invalid_item_count += 1
                metrics.errors.append(f"核心字段缺失: {','.join(missing)} - {normalized.get('title') or normalized.get('url')}")
                continue
            if not normalized.get("content"):
                metrics.empty_content_count += 1
            normalized_items.append(normalized)

    normalized_items, duplicate_count = dedupe_policy_items(normalized_items)
    metrics.duplicate_policy_count += duplicate_count

    normalized_latest_items = []
    for item in latest_items or extract_latest_items_from_output(output):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        pub_at = parse_date(item.get("pub_at"))
        normalized_latest_items.append(
            {
                "title": title,
                "pub_at": pub_at.isoformat() if pub_at else "",
            }
        )
        if len(normalized_latest_items) >= 5:
            break

    if storage_result is None:
        storage_result = extract_storage_result_from_output(output, len(normalized_items))
    saved_count = storage_result.get("saved_count") if isinstance(storage_result, dict) else None
    metrics.saved_count = saved_count if isinstance(saved_count, int) else len(normalized_items)

    if isinstance(api_push_result, dict) and api_push_result.get("status") == "error":
        metrics.api_push_failed_count += 1

    return {
        "items": normalized_items,
        "latest_items": normalized_latest_items,
        "metrics": metrics.to_dict(),
        "storage_result": storage_result,
        "api_push_result": api_push_result,
    }
