"""训记 Open API client: auth, rate limits, training/plan read, upsert wrapper."""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

from paths import ENV_PATH, ROOT

TRAINS_BASE = "https://trains.xunjiapp.cn"
API_BASE = "https://api.xunjiapp.cn"

# Minimum intervals between requests (seconds), per Skill rules.
INTERVAL_READ = 15.0
INTERVAL_FULL = 30.0
INTERVAL_WRITE = 45.0
INTERVAL_PLAN = 15.0

MAX_RETRIES = 3


class XunjiApiError(RuntimeError):
    def __init__(self, message: str, payload: Any | None = None):
        super().__init__(message)
        self.payload = payload


class XunjiClient:
    def __init__(self, api_key: str | None = None, timeout: float = 60.0):
        load_dotenv(ENV_PATH)
        self.api_key = api_key or os.getenv("XUNJI_API_KEY", "").strip()
        if not self.api_key:
            raise XunjiApiError(
                "Missing XUNJI_API_KEY. Copy .env.example to .env and set the key."
            )
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "gym-xunji-sync/1.0",
            },
        )
        self._last_request_at: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> XunjiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _wait(self, bucket: str, interval: float) -> None:
        last = self._last_request_at.get(bucket, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < interval:
            time.sleep(interval - elapsed)

    def _mark(self, bucket: str) -> None:
        self._last_request_at[bucket] = time.monotonic()

    @staticmethod
    def _decode_json(response: httpx.Response) -> dict[str, Any]:
        content = response.content
        if not content:
            raise XunjiApiError("Empty response", None)
        # httpx usually decompresses; handle raw gzip if needed.
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        try:
            data = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise XunjiApiError(f"Invalid JSON: {exc}", content[:200]) from exc
        if not isinstance(data, dict):
            raise XunjiApiError("Response root is not an object", data)
        return data

    @staticmethod
    def _is_rate_limited(data: dict[str, Any], status_code: int) -> bool:
        if status_code == 429:
            return True
        text = json.dumps(data, ensure_ascii=False).lower()
        return "too frequent" in text or "rate limit" in text or "too_frequent" in text

    @staticmethod
    def _retry_after_seconds(data: dict[str, Any], default: float) -> float:
        for key in ("retry_after_ms", "retryAfterMs", "retry_after"):
            if key in data and data[key] is not None:
                val = float(data[key])
                return val / 1000.0 if val > 100 else val
        err = data.get("error") or data.get("message") or ""
        if isinstance(err, dict):
            ms = err.get("retry_after_ms") or err.get("retryAfterMs")
            if ms is not None:
                return float(ms) / 1000.0
        return default

    def _post(
        self,
        url: str,
        body: dict[str, Any],
        *,
        bucket: str,
        interval: float,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait(bucket, interval)
            try:
                response = self._client.post(url, json=body)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(interval)
                continue
            finally:
                self._mark(bucket)

            data = self._decode_json(response)
            if self._is_rate_limited(data, response.status_code):
                wait = self._retry_after_seconds(data, interval)
                print(f"[xunji] rate limited ({bucket}), sleep {wait:.1f}s (attempt {attempt})")
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                raise XunjiApiError(
                    f"HTTP {response.status_code} for {url}: {data}",
                    data,
                )

            # Success: prefer presence of res; do not require success===true.
            if "res" in data or response.status_code == 200:
                return data

            raise XunjiApiError(f"Unexpected response for {url}", data)

        raise XunjiApiError(
            f"Failed after {MAX_RETRIES} retries for {url}: {last_error}",
            None,
        )

    def get_training(
        self, datestr: str, *, include_full_data: bool = True
    ) -> dict[str, Any]:
        # Skill limits per training day; use a global bucket when syncing many days
        # so we never burst consecutive dates faster than the full/lite interval.
        interval = INTERVAL_FULL if include_full_data else INTERVAL_READ
        bucket = "train:full" if include_full_data else "train:lite"
        return self._post(
            f"{TRAINS_BASE}/api_trains_for_llm_v2",
            {
                "schema_version": "train_open_api_v2",
                "datestr": datestr,
                "include_full_data": include_full_data,
            },
            bucket=bucket,
            interval=interval,
        )

    def list_plans(self) -> dict[str, Any]:
        return self._post(
            f"{API_BASE}/open/plan/query_gzip",
            {"schema_version": "plan_open_api_v1", "action": "list"},
            bucket="plan:list",
            interval=INTERVAL_PLAN,
        )

    def get_plan(
        self,
        plan_ref: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        include_movements: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema_version": "plan_open_api_v1",
            "action": "get",
            "plan_ref": plan_ref,
            "include_movements": include_movements,
        }
        if start_date:
            body["start_date"] = start_date
        if end_date:
            body["end_date"] = end_date
        return self._post(
            f"{API_BASE}/open/plan/query_gzip",
            body,
            bucket=f"plan:get:{plan_ref}",
            interval=INTERVAL_PLAN,
        )

    def upsert_trains(
        self,
        trains: list[dict[str, Any]] | dict[str, Any],
        *,
        dry_run: bool = False,
        include_full_data: bool = False,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Write-back wrapper only — no CLI. Caller must confirm with user first."""
        return self._post(
            f"{TRAINS_BASE}/api_upsert_trains_for_llm_v2",
            {
                "schema_version": "train_open_api_v2",
                "client_request_id": client_request_id or str(uuid.uuid4()),
                "dry_run": dry_run,
                "include_full_data": include_full_data,
                "res": trains,
            },
            bucket="train:upsert",
            interval=INTERVAL_WRITE,
        )


def main() -> None:
    """Smoke: load key and print project root (no network)."""
    load_dotenv(ENV_PATH)
    key = os.getenv("XUNJI_API_KEY", "")
    print(f"root={ROOT}")
    print(f"key_loaded={bool(key)}")


if __name__ == "__main__":
    main()
