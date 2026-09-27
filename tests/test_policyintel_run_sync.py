import os
import unittest
from unittest.mock import Mock, patch

from crawler_manager import CrawlerManager
from db_utils import DBUtils


class PolicyIntelRunSyncTests(unittest.TestCase):
    def setUp(self):
        self.db = DBUtils()
        self.record = {
            "run_id": "run-1",
            "crawler_key": "crawler.py",
        }
        self.env = {
            "POLICYINTEL_SYNC_ENABLED": "1",
            "POLICYINTEL_API_BASE_URL": "http://127.0.0.1:5173/api",
            "CRAWLER_API_KEY": "test-key",
        }

    @staticmethod
    def response(status_code, payload=None, text="", headers=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.headers = headers or {}
        if payload is None:
            response.json.side_effect = ValueError("invalid json")
        else:
            response.json.return_value = payload
        return response

    def test_success_returns_structured_result(self):
        response = self.response(
            200,
            {"success": True, "message": "运行记录已保存"},
        )
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            return_value=response,
        ) as post:
            result = self.db.push_crawler_run_to_policyintel(self.record)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(post.call_count, 1)

    def test_unauthorized_is_not_retried(self):
        response = self.response(401, text="Unauthorized")
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            return_value=response,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_crawler_run_to_policyintel(self.record)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 401)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_run_sync_is_disabled_when_switch_is_not_set(self):
        env = {
            "POLICYINTEL_API_BASE_URL": "http://127.0.0.1:5173/api",
            "CRAWLER_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "db_utils.requests.post",
        ) as post:
            result = self.db.push_crawler_run_to_policyintel(self.record)

        self.assertEqual(result["status"], "skipped")
        self.assertIn("开关未开启", result["message"])
        post.assert_not_called()

    def test_server_error_is_retried_then_succeeds(self):
        responses = [
            self.response(500, text="temporary"),
            self.response(200, {"success": True, "message": "已恢复"}),
        ]
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=responses,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_crawler_run_to_policyintel(self.record)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_rate_limit_honors_retry_after(self):
        responses = [
            self.response(
                429,
                text="Too Many Requests",
                headers={"Retry-After": "9"},
            ),
            self.response(200, {"success": True, "message": "已恢复"}),
        ]
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=responses,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_crawler_run_to_policyintel(self.record)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(9.0)

    def test_supabase_top_level_result_is_preserved(self):
        self.db.supabase_url = None
        self.db.supabase_key = None
        policyintel_result = {
            "status": "error",
            "message": "PolicyIntel unavailable",
        }
        with patch.object(
            self.db,
            "push_crawler_run_to_policyintel",
            return_value=policyintel_result,
        ):
            result = self.db.save_crawler_run(self.record)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(
            result["message"],
            "Supabase credentials are not configured",
        )
        self.assertEqual(result["policyintel_result"], policyintel_result)

    def test_run_record_failure_does_not_trigger_crawler_retry(self):
        result = {
            "status": "success",
            "metrics": {
                "target_date_count": 0,
                "filtered_count": 1,
                "empty_content_count": 0,
                "errors": [],
            },
            "latest_items": [{"title": "example", "pub_at": "2026-09-27"}],
            "run_record_result": {
                "status": "success",
                "policyintel_result": {
                    "status": "error",
                    "message": "PolicyIntel unavailable",
                },
            },
        }

        self.assertIsNone(CrawlerManager._failure_reason(result))

    def test_policy_write_failure_does_not_trigger_crawler_retry(self):
        result = {
            "status": "success",
            "metrics": {
                "target_date_count": 1,
                "filtered_count": 0,
                "empty_content_count": 0,
                "errors": [],
            },
            "latest_items": [{"title": "example", "pub_at": "2026-09-27"}],
            "policyintel_result": {
                "status": "error",
                "message": "PolicyIntel unavailable",
            },
        }

        self.assertIsNone(CrawlerManager._failure_reason(result))


if __name__ == "__main__":
    unittest.main()
