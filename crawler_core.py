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


def beijing_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def external_send_enabled() -> bool:
    return os.getenv("POLICYCLAW_ENABLE_EXTERNAL_SEND", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_crawl_date_window(today: Optional[date] = None) -> Tuple[date, date]:
    today = today or beijing_now().date()
    single_date = os.getenv("CRAWL_DATE")
    date_from = os.getenv("CRAWL_DATE_FROM")
    date_to = os.getenv("CRAWL_DATE_TO")

    if single_date:
        parsed = parse_date(single_date)
        if not parsed:
            raise ValueError("CRAWL_DATE 必须是 YYYY-MM-DD 格式")
        return parsed, parsed

    if date_from or date_to:
        start = parse_date(date_from) if date_from else today - timedelta(days=7)
        end = parse_date(date_to) if date_to else today - timedelta(days=1)
        if not start or not end:
            raise ValueError("CRAWL_DATE_FROM/CRAWL_DATE_TO 必须是 YYYY-MM-DD 格式")
        if start > end:
            raise ValueError("CRAWL_DATE_FROM 不能晚于 CRAWL_DATE_TO")
        return start, end

    days = int(os.getenv("CRAWL_WINDOW_DAYS", "7"))
    days = max(1, days)
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
    metrics.target_date_count = item_count
    metrics.valid_item_count = item_count

    filter_match = re.search(
        r"(?:过滤掉|过滤非昨日数据|过滤掉非目标日期数据)\s*[:：]?\s*(\d+)\s*条",
        output,
    )
    if filter_match:
        metrics.filtered_count = int(filter_match.group(1))

    latest_count = len(re.findall(r"^✅\s+.+?\s+\d{4}-\d{2}-\d{2}\s*$", output, re.MULTILINE))
    metrics.raw_item_count = max(item_count + metrics.filtered_count, latest_count, item_count)
    if metrics.raw_item_count:
        metrics.valid_item_count = max(metrics.valid_item_count, metrics.raw_item_count - metrics.invalid_item_count)

    if item_count == 0 and metrics.filtered_count == 0:
        metrics.errors.append("filtered_count 和 target_date_count 均为 0，疑似列表未解析到有效数据")
    return metrics


def adapt_legacy_result(result: Any, output: str, source_name: str = "") -> Dict[str, Any]:
    api_push_result = None
    data_list = []

    if isinstance(result, dict) and "items" in result:
        items = result.get("items") or []
        metric_fields = {field_info.name for field_info in fields(CrawlerMetrics)}
        metrics = CrawlerMetrics(
            **{k: v for k, v in (result.get("metrics") or {}).items() if k in metric_fields}
        )
        api_push_result = result.get("api_push_result")
    elif isinstance(result, tuple) and len(result) == 2:
        data_list, api_push_result = result
        items = data_list or []
        metrics = extract_metrics_from_output(output, len(items))
    else:
        items = result or []
        metrics = extract_metrics_from_output(output, len(items))

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
    metrics.saved_count = len(normalized_items)

    if isinstance(api_push_result, dict) and api_push_result.get("status") == "error":
        metrics.api_push_failed_count += 1

    return {
        "items": normalized_items,
        "metrics": metrics.to_dict(),
        "api_push_result": api_push_result,
    }
