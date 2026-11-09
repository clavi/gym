"""Authenticated, validated clients for the four 训记 read APIs.

No client performs a request at construction time. Errors deliberately exclude
response bodies, headers and underlying HTTP exceptions (which may hold keys).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import threading
import time
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx
from dotenv import load_dotenv

if __package__:
    from .paths import ENV_PATH, ROOT
    from .responses import template_result
else:
    from paths import ENV_PATH, ROOT
    from responses import template_result

TRAINS_BASE = "https://trains.xunjiapp.cn"
API_BASE = "https://api.xunjiapp.cn"
FOOD_BASE = "https://eatings.xunjiapp.cn"
INTERVAL_READ = 15.0
INTERVAL_FULL = 30.0
INTERVAL_WRITE = 45.0
INTERVAL_PLAN = 15.0
MAX_RETRIES = 3
BODY_TYPES = frozenset({
    "weight", "bodyfat", "neck", "chest", "weist", "shoulder", "bot",
    "arm_left", "arm_right", "forearm_left", "forearm_right",
    "leg_left", "leg_right", "cav_left", "cav_right",
})

# Same-key client instances share rate limits within this process. The lock also
# prevents two threads from passing the interval check simultaneously.
_RATE_LOCK = threading.RLock()
_LAST_REQUEST: dict[tuple[str, str], float] = {}
_BLOCK_UNTIL: dict[tuple[str, str], float] = {}


class XunjiApiError(RuntimeError):
    def __init__(self, message: str, payload: Any = None, *, code: str = "api_error",
                 status_code: int | None = None):
        super().__init__(message)
        self.payload = None  # Compatibility attribute; never retain unsafe responses.
        self.code = code
        self.status_code = status_code


def previous_year(today: date) -> date:
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def validate_range(start: str, end: str) -> tuple[date, date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError("start_date must be <= end_date")
    return a, b


def validate_food_range(start: str, end: str, today: date | None = None) -> None:
    a, b = validate_range(start, end)
    today = today or date.today()
    if a < previous_year(today) or b > today + timedelta(days=90):
        raise ValueError("Food dates must be within the past year and next 90 days")


def validate_body_types(types: list[str] | None) -> list[str] | None:
    if types is None:
        return None
    if not isinstance(types, list) or not types or any(not isinstance(t, str) or t not in BODY_TYPES for t in types):
        raise ValueError("Invalid body types; the API spells waist as 'weist'")
    return sorted(set(types))


class _ApiClient:
    key_env = ""
    require_success = True

    def __init__(self, api_key: str | None = None, timeout: float = 60.0, *,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if api_key is None:
            load_dotenv(ENV_PATH)
        self.api_key = (api_key if api_key is not None else os.getenv(self.key_env, "")).strip()
        if not self.api_key:
            raise XunjiApiError(f"Missing {self.key_env} in environment or .env", code="missing_key")
        self._key_id = hashlib.sha256(self.api_key.encode()).hexdigest()
        self._sleep, self._clock = sleep, clock
        self._client = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=False,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json", "Accept-Encoding": "gzip",
                     "User-Agent": "gym-xunji-sync/2.0"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _wait(self, bucket: str, interval: float) -> None:
        key = self._key_id, bucket
        last = _LAST_REQUEST.get(key)
        due = max(last + interval if last is not None else 0, _BLOCK_UNTIL.get(key, 0))
        remaining = due - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def _mark(self, bucket: str) -> None:
        _LAST_REQUEST[self._key_id, bucket] = self._clock()

    @staticmethod
    def _decode_json(response: httpx.Response) -> dict[str, Any]:
        try:
            content = response.content
            if content[:2] == b"\x1f\x8b":
                content = gzip.decompress(content)
            data = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError, OSError, EOFError, zlib.error):
            raise XunjiApiError("Invalid JSON/gzip response", code="invalid_response") from None
        if not isinstance(data, dict):
            raise XunjiApiError("Response must be a JSON object", code="invalid_response")
        return data

    @staticmethod
    def _error_code(data: dict, status: int) -> str:
        # Text is used only for classification, never emitted or retained.
        text = json.dumps({k: data.get(k) for k in ("error", "message", "msg", "code")},
                          ensure_ascii=False).lower()
        if status == 429 or any(s in text for s in ("too frequent", "rate limit", "too_frequent")):
            return "rate_limited"
        if "cursor invalid" in text or "cursor_invalid" in text:
            return "cursor_invalid"
        if status == 401 or "apikey" in text or "api key" in text:
            return "auth_error"
        if status == 403 or "vip" in text:
            return "permission_denied"
        if status == 409 or "version conflict" in text:
            return "version_conflict"
        return "api_error"

    @staticmethod
    def _retry_after_seconds(data: dict, default: float) -> float:
        for obj in (data, data.get("error"), data.get("res")):
            if not isinstance(obj, dict):
                continue
            for key, scale in (("retry_after_ms", .001), ("retryAfterMs", .001), ("retry_after", 1)):
                try:
                    value = float(obj[key]) * scale
                    if math.isfinite(value) and value >= 0:
                        return value
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
        return default

    @staticmethod
    def _header_retry_after(response: httpx.Response) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 0.0
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0

    def _post(self, url: str, body: dict[str, Any], *, bucket: str,
              interval: float, retry_safe: bool = True) -> dict[str, Any]:
        error = XunjiApiError("Request failed", code="network_error")
        for attempt in range(MAX_RETRIES):
            try:
                with _RATE_LOCK:
                    self._wait(bucket, interval)
                    try:
                        response = self._client.post(url, json=body)
                    finally:
                        self._mark(bucket)
            except httpx.HTTPError:
                error = XunjiApiError("HTTP transport failed", code="network_error")
                if not retry_safe:
                    raise error from None
                if attempt + 1 < MAX_RETRIES:
                    self._sleep(interval)
                continue

            try:
                data = self._decode_json(response)
            except XunjiApiError:
                if response.status_code != 429 and response.status_code < 500:
                    raise
                data = {}  # HTML/empty 429 and 5xx responses must also be retried.
            code = self._error_code(data, response.status_code)
            transient = code == "rate_limited" or (response.status_code >= 500 and retry_safe)
            if transient:
                error = XunjiApiError("Rate limit or temporary server failure", code=code,
                                      status_code=response.status_code)
                delay = max(interval, self._retry_after_seconds(data, interval),
                            self._header_retry_after(response))
                with _RATE_LOCK:
                    key = self._key_id, bucket
                    _BLOCK_UNTIL[key] = max(_BLOCK_UNTIL.get(key, 0), self._clock() + delay)
                if attempt + 1 < MAX_RETRIES:
                    self._sleep(delay)
                continue
            if (not 200 <= response.status_code < 300 or data.get("success") is False
                    or (not self._has_result(data) and code != "api_error")):
                raise XunjiApiError(f"API rejected request ({code})", code=code,
                                    status_code=response.status_code)
            if not self._has_result(data) or (
                self.require_success and data.get("success") is not True
            ):
                raise XunjiApiError("Missing successful result", code="invalid_response")
            return data
        raise error from None

    def _has_result(self, payload: dict) -> bool:
        return "res" in payload and payload["res"] is not None


class XunjiClient(_ApiClient):
    """Training and official plans; retains the original script-facing API."""
    key_env = "XUNJI_API_KEY"
    require_success = False

    def get_training(self, datestr: str, *, include_full_data: bool = True) -> dict:
        date.fromisoformat(datestr)
        return self._post(
            f"{TRAINS_BASE}/api_trains_for_llm_v2",
            {"schema_version": "train_open_api_v2", "datestr": datestr,
             "include_full_data": include_full_data},
            bucket="train:read", interval=INTERVAL_FULL if include_full_data else INTERVAL_READ,
        )

    def list_plans(self) -> dict:
        return self._post(f"{API_BASE}/open/plan/query_gzip",
                          {"schema_version": "plan_open_api_v1", "action": "list"},
                          bucket="plan:list", interval=INTERVAL_PLAN)

    def get_plan(self, plan_ref: str, *, start_date: str | None = None,
                 end_date: str | None = None, include_movements: bool = True) -> dict:
        if not plan_ref.startswith(("platform:", "universal:")) or not plan_ref.split(":", 1)[1].isdigit():
            raise ValueError("Invalid plan_ref")
        if bool(start_date) != bool(end_date):
            raise ValueError("Provide both start_date and end_date")
        if start_date and end_date:
            a, b = validate_range(start_date, end_date)
            if (b - a).days >= 92:
                raise ValueError("Plan range cannot exceed 92 days")
        body = {"schema_version": "plan_open_api_v1", "action": "get",
                "plan_ref": plan_ref, "include_movements": include_movements}
        if start_date:
            body.update(start_date=start_date, end_date=end_date)
        return self._post(f"{API_BASE}/open/plan/query_gzip", body,
                          bucket=f"plan:get:{plan_ref}", interval=INTERVAL_PLAN)

    def upsert_trains(self, trains: list[dict] | dict, *, dry_run: bool = False,
                      confirmed: bool = False, include_full_data: bool = False,
                      client_request_id: str | None = None) -> dict:
        """No CLI; actual writes require explicit caller confirmation."""
        if not dry_run and not confirmed:
            raise ValueError("User confirmation required before writing training")
        return self._post(
            f"{TRAINS_BASE}/api_upsert_trains_for_llm_v2",
            {"schema_version": "train_open_api_v2", "client_request_id": client_request_id or str(uuid.uuid4()),
             "dry_run": dry_run, "include_full_data": include_full_data, "res": trains},
            bucket="train:write", interval=INTERVAL_WRITE, retry_safe=dry_run,
        )


class XunjiFoodClient(_ApiClient):
    key_env = "XUNJI_FOOD_API_KEY"

    def get_food(self, start: str, end: str, *, include_detail: bool = True) -> dict:
        validate_food_range(start, end)
        return self._post(f"{FOOD_BASE}/open/food/query_gzip",
                          {"start_date": start, "end_date": end, "include_detail": include_detail},
                          bucket="food:query", interval=INTERVAL_READ)

    def list_templates(self) -> dict:
        return self._post(f"{FOOD_BASE}/open/food/templates/list_gzip", {},
                          bucket="food:templates", interval=INTERVAL_READ)


class XunjiBodyClient(_ApiClient):
    key_env = "XUNJI_BODY_API_KEY"

    def get_body(self, start: str, end: str, *, types: list[str] | None = None,
                 include_latest: bool = True, include_records: bool = True,
                 limit: int = 500, offset: int = 0) -> dict:
        validate_range(start, end)
        types = validate_body_types(types)
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Body limit must be 1..500 and offset >= 0")
        body = {"start_date": start, "end_date": end, "include_latest": include_latest,
                "include_records": include_records, "limit": limit, "offset": offset}
        if types is not None:
            body["types"] = types
        return self._post(f"{API_BASE}/open/body/query_gzip", body,
                          bucket="body:query", interval=INTERVAL_READ)


class XunjiTemplateClient(_ApiClient):
    """Only the dedicated Agent folder, not all personal folders."""
    key_env = "XUNJI_TEMPLATE_API_KEY"
    require_success = False

    def _has_result(self, payload: dict) -> bool:
        try:
            template_result(payload)
        except ValueError:
            return False
        return True

    def sync_templates(self, cursor: int = 0, *, include_content: bool = True) -> dict:
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError("Template cursor must be a nonnegative integer")
        return self._post(
            f"{TRAINS_BASE}/api_agent_templates_sync_for_llm_v1",
            {"cursor": cursor, "limit": 15, "include_content": include_content},
            bucket="templates:sync", interval=INTERVAL_READ,
        )


def main() -> None:
    load_dotenv(ENV_PATH)
    print(f"root={ROOT}")
    for name in ("XUNJI_API_KEY", "XUNJI_FOOD_API_KEY", "XUNJI_BODY_API_KEY", "XUNJI_TEMPLATE_API_KEY"):
        print(f"{name}_configured={bool(os.getenv(name, '').strip())}")


if __name__ == "__main__":
    main()
