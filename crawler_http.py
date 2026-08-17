"""HTTP helpers for crawler-only traffic.

This module intentionally owns the third-party ``requests`` dependency so
``crawler_core`` remains importable before workflow dependencies are installed.
"""

import os
import random
import time
from urllib.parse import urlsplit

import requests


class DomainRateLimitError(requests.RequestException):
    """Raised when repeated throttling makes further requests unsafe."""


class CrawlerSession(requests.Session):
    """Requests session with per-domain pacing and bounded retry/backoff."""

    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_min = self._env_float(
            "POLICYCLAW_REQUEST_DELAY_MIN_SECONDS", 0.5, minimum=0.0
        )
        self.delay_max = self._env_float(
            "POLICYCLAW_REQUEST_DELAY_MAX_SECONDS", 1.5, minimum=0.0
        )
        if self.delay_max < self.delay_min:
            raise ValueError(
                "POLICYCLAW_REQUEST_DELAY_MAX_SECONDS 不能小于 "
                "POLICYCLAW_REQUEST_DELAY_MIN_SECONDS"
            )
        self.max_attempts = self._env_int(
            "POLICYCLAW_REQUEST_MAX_ATTEMPTS", 3, minimum=1
        )
        self.cooldown_min = self._env_float(
            "POLICYCLAW_DOMAIN_COOLDOWN_MIN_SECONDS", 30.0, minimum=0.0
        )
        self.cooldown_max = self._env_float(
            "POLICYCLAW_DOMAIN_COOLDOWN_MAX_SECONDS", 60.0, minimum=0.0
        )
        if self.cooldown_max < self.cooldown_min:
            raise ValueError(
                "POLICYCLAW_DOMAIN_COOLDOWN_MAX_SECONDS 不能小于 "
                "POLICYCLAW_DOMAIN_COOLDOWN_MIN_SECONDS"
            )
        self._last_request_at = {}
        self._throttle_counts = {}
        self._cooldown_until = {}
        self._blocked_domains = set()

    @staticmethod
    def _env_float(name, default, minimum=None):
        raw_value = os.getenv(name, "").strip()
        try:
            value = float(raw_value) if raw_value else float(default)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} 不能小于 {minimum:g}")
        return value

    @staticmethod
    def _env_int(name, default, minimum=None):
        raw_value = os.getenv(name, "").strip()
        try:
            value = int(raw_value) if raw_value else int(default)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是整数") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} 不能小于 {minimum}")
        return value

    @staticmethod
    def _domain(url):
        return urlsplit(str(url)).netloc.casefold()

    @staticmethod
    def _retry_after_seconds(response):
        value = str(response.headers.get("Retry-After") or "").strip()
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _remaining_budget():
        raw_deadline = os.getenv("POLICYCLAW_CRAWLER_DEADLINE_EPOCH", "").strip()
        if not raw_deadline:
            return None
        try:
            return float(raw_deadline) - time.time()
        except ValueError:
            return None

    def _sleep_with_budget(self, seconds):
        seconds = max(0.0, float(seconds))
        remaining = self._remaining_budget()
        if remaining is not None and seconds + 10.0 >= remaining:
            raise TimeoutError("爬虫剩余运行时间不足，停止等待和重试")
        if seconds:
            time.sleep(seconds)

    def _pace_domain(self, domain):
        if not domain:
            return
        cooldown_remaining = self._cooldown_until.get(domain, 0.0) - time.monotonic()
        if cooldown_remaining > 0:
            self._sleep_with_budget(cooldown_remaining)
        previous = self._last_request_at.get(domain)
        if previous is not None:
            target_gap = random.uniform(self.delay_min, self.delay_max)
            self._sleep_with_budget(target_gap - (time.monotonic() - previous))

    def _backoff_seconds(self, response, retry_index):
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, self.cooldown_max)
        ranges = ((2.0, 4.0), (5.0, 8.0), (10.0, 16.0))
        lower, upper = ranges[min(retry_index, len(ranges) - 1)]
        return random.uniform(lower, upper)

    def request(self, method, url, **kwargs):
        domain = self._domain(url)
        if domain in self._blocked_domains:
            raise DomainRateLimitError(f"域名 {domain} 连续返回限流状态，已停止请求")

        method_name = str(method).upper()
        attempts = self.max_attempts
        if method_name not in {"GET", "HEAD", "POST"}:
            attempts = 1

        last_error = None
        for attempt in range(attempts):
            self._pace_domain(domain)
            try:
                response = super().request(method, url, **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                self._last_request_at[domain] = time.monotonic()
                if attempt + 1 >= attempts:
                    raise
                self._sleep_with_budget(random.uniform(2.0, 4.0))
                continue

            self._last_request_at[domain] = time.monotonic()
            status_code = response.status_code
            is_throttle = status_code in {403, 429}
            if is_throttle:
                throttle_count = self._throttle_counts.get(domain, 0) + 1
                self._throttle_counts[domain] = throttle_count
                if throttle_count == 2:
                    self._cooldown_until[domain] = time.monotonic() + random.uniform(
                        self.cooldown_min, self.cooldown_max
                    )
                if throttle_count >= 3:
                    self._blocked_domains.add(domain)
                    response.close()
                    raise DomainRateLimitError(
                        f"域名 {domain} 连续 {throttle_count} 次返回 {status_code}，已停止请求"
                    )
            else:
                self._throttle_counts[domain] = 0

            retryable = status_code in self.RETRYABLE_STATUS_CODES
            if status_code == 403:
                retryable = attempt == 0
            if not retryable or attempt + 1 >= attempts:
                return response

            wait_seconds = self._backoff_seconds(response, attempt)
            response.close()
            self._sleep_with_budget(wait_seconds)

        if last_error:
            raise last_error
        raise RuntimeError("请求重试流程异常结束")
