"""南京区县列表 API 时间戳兼容回归测试。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from crawler_core import parse_date


BEIJING_TZ = timezone(timedelta(hours=8))


def parse_nanjing_api_date(raw_date):
    """复现南京区县爬虫的 API 时间戳规范化路径。"""
    try:
        parsed_datetime = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        if parsed_datetime.tzinfo is not None:
            parsed_datetime = parsed_datetime.astimezone(BEIJING_TZ)
        normalized = parsed_datetime.date()
    except (TypeError, ValueError):
        normalized = raw_date
    return parse_date(normalized)


class NanjingIsoDateTests(unittest.TestCase):
    def test_nanjing_api_date_variants(self):
        cases = [
            ("2026-05-26T06:39:00.000Z", "2026-05-26"),
            ("2026-05-25T20:30:00-04:00", "2026-05-26"),
            ("2026-05-26", "2026-05-26"),
            ("not-a-date", None),
        ]
        for raw_date, expected in cases:
            with self.subTest(raw_date=raw_date):
                parsed = parse_nanjing_api_date(raw_date)
                self.assertEqual(parsed.isoformat() if parsed else None, expected)

    def test_all_affected_crawlers_accept_z_suffix(self):
        root = Path(__file__).resolve().parents[1] / "District"
        affected = []
        for path in root.glob("nanjing_*_crawler.py"):
            source = path.read_text(encoding="utf-8-sig")
            if "datetime.fromisoformat(raw_date.replace" in source:
                affected.append(path.name)
                self.assertIn('raw_date.replace("Z", "+00:00")', source)
        # 29 个原日期异常爬虫，加上本轮改用同一 API 模板的玄武部门发文。
        self.assertEqual(len(affected), 30)


if __name__ == "__main__":
    unittest.main()
