import json
import os
import unittest
from unittest.mock import patch

import requests

from db_utils import DBUtils, POLICYINTEL_MAX_REQUEST_BYTES


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text="", headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class PolicyIntelBatchingTests(unittest.TestCase):
    def setUp(self):
        self.db = DBUtils()
        self.env = {
            "POLICYINTEL_SYNC_ENABLED": "1",
            "POLICYINTEL_API_BASE_URL": "http://127.0.0.1:5173/api",
            "CRAWLER_API_KEY": "test-key",
        }

    @staticmethod
    def item(index, content="正文"):
        return {
            "title": f"政策 {index}",
            "url": f"https://example.test/{index}",
            "pub_at": "2026-09-27",
            "content": content,
            "selected": False,
            "category": "南京",
            "source": "测试来源",
            "policy_key": f"key-{index}",
        }

    @staticmethod
    def success_response(_url, **kwargs):
        payload = json.loads(kwargs["data"].decode("utf-8"))
        count = len(payload["sources"][0]["items"])
        return FakeResponse({
            "status": "success",
            "inserted_count": count,
            "skipped_count": 0,
            "invalid_count": 0,
            "failed_count": 0,
            "errors": [],
        })

    def test_batches_respect_item_and_utf8_byte_limits(self):
        items = [self.item(index, "政" * 60000) for index in range(12)]
        sent_bodies = []

        def post(url, **kwargs):
            sent_bodies.append(kwargs["data"])
            return self.success_response(url, **kwargs)

        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=post,
        ):
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["inserted_count"], 12)
        self.assertGreater(len(sent_bodies), 1)
        for body in sent_bodies:
            payload = json.loads(body.decode("utf-8"))
            batch_items = payload["sources"][0]["items"]
            self.assertLessEqual(len(batch_items), 100)
            self.assertLessEqual(len(body), POLICYINTEL_MAX_REQUEST_BYTES)

    def test_single_oversized_policy_is_not_truncated_or_sent(self):
        items = [self.item(1, "政" * 600000)]

        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
        ) as post:
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("未截断正文且未发送", result["errors"][0])
        post.assert_not_called()

    def test_sync_is_disabled_when_switch_is_not_set(self):
        env = {
            "POLICYINTEL_API_BASE_URL": "http://127.0.0.1:5173/api",
            "CRAWLER_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "db_utils.requests.post",
        ) as post:
            result = self.db.push_to_policyintel(
                [self.item(1)],
                "测试来源",
            )

        self.assertEqual(result["status"], "skipped")
        self.assertIn("开关未开启", result["message"])
        post.assert_not_called()

    def test_transient_server_error_is_retried(self):
        items = [self.item(1)]
        responses = [
            FakeResponse(status_code=500, text="temporary"),
            FakeResponse({
                "status": "success",
                "inserted_count": 1,
                "skipped_count": 0,
                "invalid_count": 0,
                "failed_count": 0,
                "errors": [],
            }),
        ]
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=responses,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_network_timeout_is_retried(self):
        items = [self.item(1)]
        success = FakeResponse({
            "status": "success",
            "inserted_count": 1,
            "skipped_count": 0,
            "invalid_count": 0,
            "failed_count": 0,
            "errors": [],
        })
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=[requests.exceptions.Timeout("timeout"), success],
        ) as post, patch("db_utils.time.sleep"):
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(post.call_count, 2)

    def test_client_error_is_not_retried(self):
        items = [self.item(1)]
        response = FakeResponse(status_code=401, text="Unauthorized")
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            return_value=response,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_structured_partial_response_is_not_retried(self):
        items = [self.item(1)]
        response = FakeResponse({
            "status": "partial",
            "inserted_count": 0,
            "skipped_count": 0,
            "invalid_count": 1,
            "failed_count": 0,
            "errors": ["invalid item"],
        })
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            return_value=response,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["invalid_count"], 1)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_rate_limit_honors_retry_after(self):
        items = [self.item(1)]
        responses = [
            FakeResponse(
                status_code=429,
                text="Too Many Requests",
                headers={"Retry-After": "12"},
            ),
            FakeResponse({
                "status": "success",
                "inserted_count": 1,
                "skipped_count": 0,
                "invalid_count": 0,
                "failed_count": 0,
                "errors": [],
            }),
        ]
        with patch.dict(os.environ, self.env, clear=False), patch(
            "db_utils.requests.post",
            side_effect=responses,
        ) as post, patch("db_utils.time.sleep") as sleep:
            result = self.db.push_to_policyintel(items, "测试来源")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(12.0)


if __name__ == "__main__":
    unittest.main()
