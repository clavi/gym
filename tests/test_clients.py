"""Offline HTTP contract/error tests; no real API keys or network requests."""

import gzip
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import xunji_client as api


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class ClientTests(unittest.TestCase):
    def setUp(self):
        api._LAST_REQUEST.clear()
        api._BLOCK_UNTIL.clear()
        self.clock = Clock()
        self.requests = []

    def client(self, cls=api.XunjiClient, responses=None, handler=None):
        responses = iter(responses or [])

        def receive(request):
            self.requests.append(request)
            if handler:
                return handler(request)
            return next(responses)

        client = cls(api_key="test-only-key", transport=httpx.MockTransport(receive),
                     clock=self.clock, sleep=self.clock.sleep)
        self.addCleanup(client.close)
        return client

    def test_training_does_not_require_success_flag(self):
        client = self.client(responses=[httpx.Response(200, json={"res": {"trains": []}})])
        client.get_training("2026-11-04")
        body = json.loads(self.requests[0].content)
        self.assertIs(body["include_full_data"], True)
        self.assertNotIn("test-only-key", self.requests[0].content.decode())
        self.assertEqual(self.requests[0].headers["Authorization"], "Bearer test-only-key")

    def test_training_http_200_error_is_not_empty_success(self):
        client = self.client(responses=[httpx.Response(200, json={"error": "apikey invalid"})])
        with self.assertRaises(api.XunjiApiError):
            client.get_training("2026-11-04")
        self.assertEqual(len(self.requests), 1)

    def test_explicit_failure_with_res_is_rejected(self):
        client = self.client(responses=[httpx.Response(200, json={"success": False, "res": {}})])
        with self.assertRaises(api.XunjiApiError):
            client.get_training("2026-11-04")

    def test_food_requires_explicit_success(self):
        client = self.client(api.XunjiFoodClient, [httpx.Response(200, json={"res": {"days": []}})])
        today = date.today().isoformat()
        with self.assertRaises(api.XunjiApiError):
            client.get_food(today, today)

    def test_explicit_test_key_does_not_read_dotenv(self):
        with patch.object(api, "load_dotenv", side_effect=AssertionError("Should not read real keys")):
            self.client()

    def test_food_range_validated_before_request(self):
        client = self.client(api.XunjiFoodClient)
        today = date.today()
        with self.assertRaises(ValueError):
            client.get_food((api.previous_year(today) - timedelta(days=1)).isoformat(), today.isoformat())
        with self.assertRaises(ValueError):
            client.get_food(today.isoformat(), (today + timedelta(days=91)).isoformat())
        self.assertEqual(self.requests, [])

    def test_independent_keys_never_fall_back_to_training(self):
        with patch.object(api, "load_dotenv"), patch.dict("os.environ", {"XUNJI_API_KEY": "training-key"}, clear=True):
            for cls in (api.XunjiFoodClient, api.XunjiBodyClient, api.XunjiTemplateClient):
                with self.subTest(cls=cls), self.assertRaises(api.XunjiApiError) as error:
                    cls()
                self.assertEqual(error.exception.code, "missing_key")

    def test_gzip_header(self):
        raw = gzip.compress(json.dumps({"res": {"trains": []}}).encode())
        client = self.client(responses=[httpx.Response(200, content=raw, headers={"Content-Encoding": "gzip"})])
        self.assertEqual(client.get_training("2026-11-04")["res"]["trains"], [])

    def test_gzip_without_header(self):
        raw = gzip.compress(json.dumps({"res": {"trains": []}}).encode())
        client = self.client(responses=[httpx.Response(200, content=raw)])
        self.assertEqual(client.get_training("2026-11-04")["res"]["trains"], [])

    def test_invalid_json_and_gzip_are_sanitized(self):
        for raw in (b"test-only-key <html>", b"\x1f\x8bnot-gzip", b"[]", b"\xff"):
            with self.subTest(raw=raw):
                client = self.client(responses=[httpx.Response(200, content=raw)])
                with self.assertRaises(api.XunjiApiError) as error:
                    client.get_training("2026-11-04")
                self.assertEqual(error.exception.code, "invalid_response")
                self.assertNotIn("test-only-key", str(error.exception))
                self.assertIsNone(error.exception.payload)

    def test_nested_retry_milliseconds_not_confused_with_seconds(self):
        self.assertEqual(api._ApiClient._retry_after_seconds({"retry_after_ms": 50}, 15), .05)
        self.assertEqual(api._ApiClient._retry_after_seconds({"error": {"retry_after_ms": 1250}}, 15), 1.25)
        self.assertEqual(api._ApiClient._retry_after_seconds({"retry_after_ms": "bad"}, 15), 15)

    def test_rate_limit_obeys_retry_hint(self):
        client = self.client(responses=[
            httpx.Response(429, json={"error": {"retry_after_ms": 45000}}),
            httpx.Response(200, json={"res": {"trains": []}}),
        ])
        client.get_training("2026-11-04")
        self.assertEqual(self.clock.waits, [45])

    def test_non_json_rate_limit_obeys_header(self):
        client = self.client(responses=[
            httpx.Response(429, text="<html>", headers={"Retry-After": "60"}),
            httpx.Response(200, json={"res": {"trains": []}}),
        ])
        client.get_training("2026-11-04")
        self.assertEqual(self.clock.waits, [60])

    def test_server_retries_are_bounded(self):
        client = self.client(responses=[httpx.Response(503, text="Unavailable") for _ in range(3)])
        with self.assertRaises(api.XunjiApiError) as error:
            client.get_training("2026-11-04")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(error.exception.status_code, 503)

    def test_auth_error_does_not_retry_or_leak_response(self):
        client = self.client(responses=[httpx.Response(401, json={"error": "apikey invalid test-only-key"})])
        with self.assertRaises(api.XunjiApiError) as error:
            client.get_training("2026-11-04")
        self.assertEqual(error.exception.code, "auth_error")
        self.assertNotIn("test-only-key", str(error.exception))
        self.assertEqual(len(self.requests), 1)

    def test_transport_error_is_sanitized(self):
        def fail(request):
            raise httpx.ConnectError("test-only-key", request=request)
        client = self.client(handler=fail)
        with self.assertRaises(api.XunjiApiError) as error:
            client.get_training("2026-11-04")
        self.assertEqual(error.exception.code, "network_error")
        self.assertNotIn("test-only-key", str(error.exception))
        self.assertEqual(len(self.requests), 3)

    def test_same_key_instances_share_rate_limit(self):
        a = self.client(responses=[httpx.Response(200, json={"res": {"trains": []}})])
        b = self.client(responses=[httpx.Response(200, json={"res": {"trains": []}})])
        a.get_training("2026-11-04")
        b.get_training("2026-11-05")
        self.assertEqual(self.clock.waits, [30])

    def test_exhausted_rate_limit_keeps_cooldown_for_next_client(self):
        a = self.client(responses=[httpx.Response(429, headers={"Retry-After": "60"}) for _ in range(3)])
        with self.assertRaises(api.XunjiApiError):
            a.get_training("2026-11-04")
        b = self.client(responses=[httpx.Response(200, json={"res": {"trains": []}})])
        b.get_training("2026-11-04")
        self.assertEqual(self.clock.waits, [60, 60, 60])

    def test_training_writes_require_confirmation(self):
        client = self.client()
        with self.assertRaises(ValueError):
            client.upsert_trains([])
        self.assertEqual(self.requests, [])

    def test_non_dry_run_writes_do_not_retry_ambiguous_failure(self):
        def fail(request):
            raise httpx.ReadTimeout("secret", request=request)
        client = self.client(handler=fail)
        with self.assertRaises(api.XunjiApiError):
            client.upsert_trains([], confirmed=True)
        self.assertEqual(len(self.requests), 1)

    def test_body_contract_and_historical_waist_spelling(self):
        client = self.client(api.XunjiBodyClient, [httpx.Response(200, json={"success": True, "res": {"records": []}})])
        client.get_body("2026-01-01", "2026-11-04", types=["weight", "weist", "weight"], offset=10)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["types"], ["weight", "weist"])
        self.assertEqual(body["offset"], 10)
        with self.assertRaises(ValueError):
            client.get_body("2026-01-01", "2026-11-04", types=["waist"])
        with self.assertRaises(ValueError):
            client.get_body("2026-01-01", "2026-11-04", limit=501)

    def test_template_page_limit_is_always_fifteen(self):
        client = self.client(api.XunjiTemplateClient, [httpx.Response(200, json={"res": {}})])
        client.sync_templates(123, include_content=False)
        self.assertEqual(json.loads(self.requests[0].content), {"cursor": 123, "limit": 15, "include_content": False})
        with self.assertRaises(ValueError):
            client.sync_templates(-1)

    def test_live_root_template_response_is_returned_unchanged(self):
        payload = {"schema_version": "agent_personal_templates_v1", "from_cursor": 0,
                   "next_cursor": 1, "current_revision": 1, "has_more": False, "changes": [],
                   "folder": {"unique_id": "test-folder"}, "limits": {"templates_per_folder": 14}}
        client = self.client(api.XunjiTemplateClient, [httpx.Response(200, json=payload)])
        self.assertEqual(client.sync_templates(), payload)

    def test_unknown_root_template_response_is_rejected(self):
        payload = {"schema_version": "unknown", "next_cursor": 1, "has_more": False, "changes": []}
        client = self.client(api.XunjiTemplateClient, [httpx.Response(200, json=payload)])
        with self.assertRaises(api.XunjiApiError):
            client.sync_templates()

    def test_invalid_cursor_is_classified_without_resetting(self):
        client = self.client(api.XunjiTemplateClient, [httpx.Response(200, json={"success": False, "error": "cursor invalid"})])
        with self.assertRaises(api.XunjiApiError) as error:
            client.sync_templates(123)
        self.assertEqual(error.exception.code, "cursor_invalid")
        self.assertEqual(len(self.requests), 1)

    def test_plan_range_is_not_silently_truncated(self):
        client = self.client()
        with self.assertRaises(ValueError):
            client.get_plan("platform:1", start_date="2026-01-01", end_date="2026-12-31")
        self.assertEqual(self.requests, [])

    def test_leap_year_boundary(self):
        self.assertEqual(api.previous_year(date(2024, 2, 29)), date(2023, 2, 28))


if __name__ == "__main__":
    unittest.main()
