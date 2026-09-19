"""Offline mock-transport tests for model_adapter (P5-BASELINE-RUN-01).

Zero real network calls: transport injected. Covers typed faults (no key /
auth 401 / HTTP 5xx / transport / schema), redaction, params hash stability,
ledger row, retry-on-transient behavior.
"""

from __future__ import annotations

import json
import os
import pathlib
import unittest

from research_tool.nine_loop import model_adapter as M

MSG = [{"role": "user", "content": "hello"}]


def ok_transport(body_extra=None):
    def t(url, body, key):
        data = {
            "id": "chatcmpl-1",
            "model": M.MODEL_ID,
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        if body_extra:
            data.update(body_extra)
        return 200, json.dumps(data).encode()

    return t


class TestNoKey(unittest.TestCase):
    def test_missing_key_typed_fault(self):
        env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_V4_API_KEY"}
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            with self.assertRaises(M.ModelFault) as ctx:
                M.chat(MSG, transport=ok_transport())
            self.assertEqual(ctx.exception.code, M.E_NO_KEY)
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestAuthAndHttp(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("DEEPSEEK_V4_API_KEY"):
            os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-mock-key"

    def test_401_auth_fault(self):
        def t(url, body, key):
            return 401, b'{"error": "bad key"}'

        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t)
        self.assertEqual(ctx.exception.code, M.E_AUTH)
        self.assertEqual(ctx.exception.http_status, 401)

    def test_500_http_fault(self):
        def t(url, body, key):
            return 500, b"boom"

        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t)
        self.assertEqual(ctx.exception.code, M.E_HTTP)


class TestSuccess(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("DEEPSEEK_V4_API_KEY"):
            os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-mock-key"

    def test_ok_transport_returns_content_and_ledger(self):
        r = M.chat(MSG, transport=ok_transport())
        self.assertEqual(r.content, "pong")
        self.assertEqual(r.usage["total_tokens"], 7)
        row = r.ledger_row()
        self.assertEqual(row["model"], M.MODEL_ID)
        self.assertEqual(row["total_tokens"], 7)
        self.assertEqual(len(row["params_sha"]), 64)

    def test_params_hash_stable(self):
        a = M.params_hash("m", MSG, 0.0, 100)
        b = M.params_hash("m", MSG, 0.0, 100)
        c = M.params_hash("m", MSG, 0.1, 100)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_request_payload_shape(self):
        captured = {}

        def t(url, body, key):
            captured["url"] = url
            captured["body"] = json.loads(body)
            captured["key"] = key
            return 200, json.dumps(
                {
                    "id": "x",
                    "model": M.MODEL_ID,
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {},
                }
            ).encode()

        M.chat(MSG, transport=t, temperature=0.0, max_tokens=64)
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["body"]["model"], M.MODEL_ID)
        self.assertEqual(captured["body"]["temperature"], 0.0)
        self.assertEqual(captured["body"]["max_tokens"], 64)
        # key never appears in payload body
        self.assertNotIn(captured["key"], json.dumps(captured["body"]))


class TestSchemaFault(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("DEEPSEEK_V4_API_KEY"):
            os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-mock-key"

    def test_bad_response_shape(self):
        def t(url, body, key):
            return 200, b'{"unexpected": true}'

        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t)
        self.assertEqual(ctx.exception.code, M.E_SCHEMA)


class TestRedaction(unittest.TestCase):
    def test_redact_strips_key_value(self):
        old = os.environ.get("DEEPSEEK_V4_API_KEY")
        os.environ["DEEPSEEK_V4_API_KEY"] = "sk-test-secret-123"
        try:
            self.assertIn("[REDACTED]", M.redact("err with sk-test-secret-123"))
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_V4_API_KEY", None)
            else:
                os.environ["DEEPSEEK_V4_API_KEY"] = old

    def test_transport_error_message_redacted(self):
        old = os.environ.get("DEEPSEEK_V4_API_KEY")
        os.environ["DEEPSEEK_V4_API_KEY"] = "sk-leaky"
        try:

            def t(url, body, key):
                # simulate a transport that echoes the key in an error body
                return 500, b"bad request sk-leaky"

            with self.assertRaises(M.ModelFault) as ctx:
                M.chat(MSG, transport=t)
            self.assertNotIn("sk-leaky", ctx.exception.safe_message)
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_V4_API_KEY", None)
            else:
                os.environ["DEEPSEEK_V4_API_KEY"] = old


class TestBackoffC1(unittest.TestCase):
    """C1: 429 exponential backoff retry — offline mock sequences."""

    def setUp(self):
        if not os.environ.get("DEEPSEEK_V4_API_KEY"):
            os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-mock-key"

    def _ok(self):
        return 200, json.dumps(
            {
                "id": "x",
                "model": M.MODEL_ID,
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 10},
            }
        ).encode()

    def test_429_then_success_retries(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            if len(calls) <= 2:
                return 429, b'{"error":"rate limit"}'
            return self._ok()

        # wall budget large enough; 2 retries then success
        r = M.chat(MSG, transport=t, wall_budget_s=120)
        self.assertEqual(r.content, "ok")
        self.assertEqual(len(calls), 3)

    def test_429_exhausted_after_max_retries(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            return 429, b'{"error":"rate limit"}'

        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t, wall_budget_s=120)
        self.assertEqual(ctx.exception.code, M.E_HTTP)
        self.assertEqual(ctx.exception.http_status, 429)
        # 1 initial + BACKOFF_MAX_RETRIES retries = 5 attempts
        self.assertEqual(len(calls), M.BACKOFF_MAX_RETRIES + 1)

    def test_retry_after_header_respected(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            if len(calls) == 1:
                return 429, b'{"error":"slow down"}', {"Retry-After": "1"}
            return self._ok()

        calls2 = []

        def t2(url, body, key):
            calls2.append(1)
            if len(calls2) == 1:
                return 429, b'{"error":"slow down"}'
            return self._ok()

        d1 = M._backoff_delay(0, retry_after=20)
        d2 = M._backoff_delay(0, retry_after=None)
        self.assertGreaterEqual(d1, 20)  # header wins over computed
        self.assertLess(d2, 20)

    def test_wall_budget_insufficient_for_backoff(self):
        def t(url, body, key):
            return 429, b'{"error":"rate limit"}'

        # wall budget smaller than any backoff delay → immediate typed fault
        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t, wall_budget_s=0.0)
        self.assertEqual(ctx.exception.code, M.E_HTTP)
        self.assertEqual(ctx.exception.http_status, 429)

    def test_non_429_unchanged_immediate_fault(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            return 500, b"boom"

        with self.assertRaises(M.ModelFault) as ctx:
            M.chat(MSG, transport=t, wall_budget_s=120)
        self.assertEqual(ctx.exception.code, M.E_HTTP)
        self.assertEqual(len(calls), 1)  # no retry on direct 500


class TestScopeHygiene(unittest.TestCase):
    def test_no_third_party_imports(self):
        src = (pathlib.Path(__file__).resolve().parent.parent / "nine_loop/model_adapter.py").read_text(
            encoding="utf-8"
        )
        for banned in ("openai", "requests", "httpx", "anthropic"):
            self.assertNotIn(f"import {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
