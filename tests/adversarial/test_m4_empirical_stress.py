"""Milestone 4 Adversarial Stress Testing Suite: Protocol Framing & Zero-Binary Isolation.

Empirical Challenger M4-2 (challenger_m4_2) verification of rt_identity_adapter.py:
1. Line length limits (>1MiB -> limit_line, id: null)
2. Stream no-LF framing (>8MiB -> AdapterProtocolFatal exit 2)
3. Malformed JSON payloads (malformed_json, id: null)
4. JSON nesting depth (>8 -> limit_depth, carries request id)
5. Large payload fields (>64KB -> limit_field, carries request id)
6. Single request hits limits (>1000 -> limit_count, carries request id)
7. Batch records limit (>10000 -> limit_count, id: null)
8. Non-UTF8 inputs and truncated multi-byte sequences (AdapterProtocolFatal exit 2)
9. Zero-binary isolation: strictly zero external binary execution / subprocess dependency
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from research_tool.nine_loop import rt_identity_adapter as A


def make_request(
    req_id: str = "req-001",
    op: str = "identify",
    n_hits: int = 1,
    url_prefix: str = "https://example.com/item",
    extra: dict | None = None,
) -> dict:
    hits = [
        {
            "url": f"{url_prefix}/{i}",
            "title": f"Title {i}",
            "snippet": f"Snippet {i}",
            "source_engine": "web",
            "audit_engine": "web",
            "rank": i,
            "query_id": "q-1",
            "content": None,
        }
        for i in range(n_hits)
    ]
    req = {
        "v": 1,
        "id": req_id,
        "op": op,
        "hits": hits,
    }
    if extra:
        req.update(extra)
    return req


def encode_requests(reqs: list[dict]) -> bytes:
    return A.AdapterClient.encode_requests(reqs)


class TestAdversarialLineLengthLimits(unittest.TestCase):
    """Stress testing 1MiB (1,048,576 bytes) line length boundaries."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_line_boundary_exactly_1mib_accepted(self):
        """A line of exactly 1MiB (1,048,576 bytes) should not trigger limit_line."""
        # Create a hit where total line length equals exactly 1,048,576 bytes
        base_req = make_request("req-exact-1m")
        base_json = json.dumps(base_req, separators=(",", ":"))
        # Pad title to reach exact size
        needed = 1_048_576 - len(base_json.encode("utf-8"))
        self.assertGreater(needed, 0)
        # Note: field limit is 65536, so split padding across content or multiple hits if needed
        # Or construct a valid JSON payload where total line is 1,048,576 bytes
        # Each hit field <= 65536
        hits = []
        for i in range(17):
            hits.append({
                "url": f"https://example.com/pad/{i}",
                "title": "p" * 60000,
                "snippet": "",
                "source_engine": "web",
                "audit_engine": "web",
                "rank": i,
                "query_id": "q",
                "content": None,
            })
        req = {"v": 1, "id": "req-1m", "op": "identify", "hits": hits}
        raw = json.dumps(req, separators=(",", ":")).encode("utf-8")
        pad_needed = 1_048_576 - len(raw)
        if pad_needed > 0:
            req["hits"][0]["snippet"] = "s" * pad_needed
            raw = json.dumps(req, separators=(",", ":")).encode("utf-8")
        self.assertEqual(len(raw), 1_048_576)
        
        res = self.engine.run_raw(raw + b"\n")
        self.assertEqual(len(res.records), 1)
        # Should be processed (not limit_line)
        self.assertNotEqual(res.records[0].get("error", {}).get("code"), "limit_line")
        self.assertTrue(res.records[0]["ok"])

    def test_line_boundary_1mib_plus_one_triggers_limit_line(self):
        """A line of 1,048,577 bytes must trigger limit_line with null id (pre-parse)."""
        payload = b"X" * (1_048_576 + 1) + b"\n"
        res = self.engine.run_raw(payload)
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertIsNone(rec["id"])
        self.assertEqual(rec["error"]["code"], "limit_line")
        self.assertEqual(rec["error"]["safe_message"], "line limit exceeded")

    def test_line_oversize_interleaved_in_batch(self):
        """Oversize lines interleaved with valid requests must not corrupt state or order."""
        req1 = make_request("req-ok-1")
        req2 = make_request("req-ok-2")
        raw1 = encode_requests([req1])
        raw2 = encode_requests([req2])
        oversize = b"Y" * (1_048_576 + 500) + b"\n"
        
        batch = raw1 + oversize + raw2
        res = self.engine.run_raw(batch)
        self.assertEqual(len(res.records), 3)
        self.assertTrue(res.records[0]["ok"])
        self.assertEqual(res.records[0]["id"], "req-ok-1")
        
        self.assertFalse(res.records[1]["ok"])
        self.assertIsNone(res.records[1]["id"])
        self.assertEqual(res.records[1]["error"]["code"], "limit_line")
        
        self.assertTrue(res.records[2]["ok"])
        self.assertEqual(res.records[2]["id"], "req-ok-2")


class TestAdversarialStreamNoLFFraming(unittest.TestCase):
    """Stress testing 8MiB stream no-LF protocol fatal framing."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_stream_exceeding_8mib_without_newline_exits_code_2(self):
        """Input stream exceeding 8MiB without newline must raise AdapterProtocolFatal(2)."""
        oversize = b"Z" * (8 * 1024 * 1024 + 1)
        with self.assertRaises(A.AdapterProtocolFatal) as ctx:
            self.engine.run_raw(oversize)
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn("8MiB", ctx.exception.stderr_excerpt)

    def test_stream_exactly_8mib_without_newline_does_not_fatal(self):
        """Input stream of exactly 8MiB (8,388,608 bytes) without newline is handled (e.g. limit_line)."""
        exact_8m = b"A" * (8 * 1024 * 1024)
        # Should not raise AdapterProtocolFatal(2), but fall through to line length limit
        res = self.engine.run_raw(exact_8m)
        self.assertEqual(len(res.records), 1)
        self.assertFalse(res.records[0]["ok"])
        self.assertEqual(res.records[0]["error"]["code"], "limit_line")


class TestAdversarialMalformedJson(unittest.TestCase):
    """Stress testing malformed, truncated, and non-conforming JSON inputs."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_truncated_json_payload(self):
        """Truncated JSON must emit malformed_json with id=null."""
        payload = b'{"v":1,"id":"trunc-1","op":"identify","hits":[{"url":' + b"\n"
        res = self.engine.run_raw(payload)
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertIsNone(rec["id"])
        self.assertEqual(rec["error"]["code"], "malformed_json")

    def test_invalid_syntax_payload(self):
        """Invalid JSON syntax must emit malformed_json with id=null."""
        payload = b'{key_without_quotes: 12345}\n'
        res = self.engine.run_raw(payload)
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertIsNone(rec["id"])
        self.assertEqual(rec["error"]["code"], "malformed_json")

    def test_non_object_root_json(self):
        """JSON array or scalar root must emit malformed_json with id=null."""
        for root_val in (b"[]\n", b'"just_a_string"\n', b"12345\n", b"true\n", b"null\n"):
            with self.subTest(root=root_val):
                res = self.engine.run_raw(root_val)
                self.assertEqual(len(res.records), 1)
                rec = res.records[0]
                self.assertFalse(rec["ok"])
                self.assertIsNone(rec["id"])
                self.assertEqual(rec["error"]["code"], "malformed_json")


class TestAdversarialNestingDepth(unittest.TestCase):
    """Stress testing JSON nesting depth limit (LIMIT_DEPTH = 8)."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_depth_8_is_accepted(self):
        """Nesting depth of exactly 8 is permitted."""
        # Top-level dict is depth 1. Nested dicts/lists add depth.
        # depth 1: {}
        # depth 2: {"hits": []}
        # depth 3: {"hits": [{}]}
        # depth 4: {"hits": [{"extra": {}}]}
        # depth 5: {"hits": [{"extra": {"l5": {}}}]}
        # depth 6: {"hits": [{"extra": {"l5": {"l6": {}}}]}
        # depth 7: {"hits": [{"extra": {"l5": {"l6": {"l7": {}}}}}
        # depth 8: {"hits": [{"extra": {"l5": {"l6": {"l7": {"l8": 1}}}}}]
        nested_8 = {"l8": 1}
        nested_7 = {"l7": nested_8}
        nested_6 = {"l6": nested_7}
        nested_5 = {"l5": nested_6}
        hit = {
            "url": "https://example.com/d8",
            "title": "d8",
            "snippet": "",
            "source_engine": "web",
            "audit_engine": "web",
            "rank": 0,
            "query_id": "q",
            "content": None,
            "extra": nested_5,
        }
        req = {"v": 1, "id": "req-depth-8", "op": "identify", "hits": [hit]}
        self.assertEqual(A.json_depth(req), 8)
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        self.assertTrue(res.records[0]["ok"])
        self.assertEqual(res.records[0]["id"], "req-depth-8")

    def test_depth_9_triggers_limit_depth_with_request_id(self):
        """Nesting depth of 9 must trigger limit_depth while preserving request id."""
        nested = 0
        for _ in range(9):
            nested = [nested]
        req = {"v": 1, "id": "req-depth-9", "op": "identify", "hits": [], "deep": nested}
        self.assertGreater(A.json_depth(req), 8)
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "req-depth-9")
        self.assertEqual(rec["error"]["code"], "limit_depth")
        self.assertEqual(rec["error"]["safe_message"], "nesting depth limit exceeded")

    def test_extreme_nesting_depth_no_recursion_crash(self):
        """Adversarial nesting depth of 50 must not crash the interpreter with RecursionError."""
        nested = 1
        for _ in range(50):
            nested = [nested]
        req = {"v": 1, "id": "req-extreme-depth", "op": "identify", "hits": [], "nest": nested}
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        self.assertEqual(res.records[0]["error"]["code"], "limit_depth")


class TestAdversarialFieldByteLimits(unittest.TestCase):
    """Stress testing 64KB (65,536 bytes) per-field limits."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_field_exactly_64kb_accepted(self):
        """Field size of exactly 65,536 bytes is accepted."""
        req = make_request("req-field-exact")
        req["hits"][0]["title"] = "T" * 65536
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        self.assertTrue(res.records[0]["ok"])

    def test_field_exceeding_64kb_triggers_limit_field(self):
        """Field size of 65,537 bytes triggers limit_field and carries request id."""
        for field in ("url", "title", "snippet", "source_engine", "audit_engine", "query_id", "content"):
            with self.subTest(field=field):
                req = make_request(f"req-field-{field}")
                req["hits"][0][field] = "x" * 65537
                res = self.engine.run([req])
                self.assertEqual(len(res.records), 1)
                rec = res.records[0]
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["id"], f"req-field-{field}")
                self.assertEqual(rec["error"]["code"], "limit_field")

    def test_top_level_field_exceeding_64kb(self):
        """Top-level op field exceeding 64KB triggers limit_field."""
        req = make_request("req-field-op")
        req["op"] = "O" * 65537
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"]["code"], "limit_field")


class TestAdversarialHitsAndBatchLimits(unittest.TestCase):
    """Stress testing single request hits limit (1,000) and batch limit (10,000)."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_hits_limit_exactly_1000_accepted(self):
        """A single request with exactly 1,000 hits must be accepted."""
        req = make_request("req-1000-hits", n_hits=1000)
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        self.assertTrue(res.records[0]["ok"])
        self.assertEqual(len(res.records[0]["identities"]), 1000)

    def test_hits_limit_1001_rejected_with_limit_count(self):
        """A single request with 1,001 hits must emit limit_count and carry request id."""
        req = make_request("req-1001-hits", n_hits=1001)
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "req-1001-hits")
        self.assertEqual(rec["error"]["code"], "limit_count")
        self.assertEqual(rec["error"]["safe_message"], "hits limit exceeded")

    def test_batch_record_limit_10001_records(self):
        """Batch with 10,001 records: first 10,000 succeed, 10,001st emits limit_count with id=null."""
        reqs = [make_request(f"b{i:05d}") for i in range(10001)]
        res = self.engine.run(reqs)
        self.assertEqual(len(res.records), 10001)
        self.assertTrue(all(r["ok"] for r in res.records[:10000]))
        last = res.records[10000]
        self.assertFalse(last["ok"])
        self.assertIsNone(last["id"])
        self.assertEqual(last["error"]["code"], "limit_count")
        self.assertEqual(last["error"]["safe_message"], "batch record limit exceeded")


class TestAdversarialNonUtf8Inputs(unittest.TestCase):
    """Stress testing invalid UTF-8 byte sequences and truncated encodings."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_raw_invalid_utf8_raises_protocol_fatal(self):
        """Non-UTF8 byte sequence must trigger AdapterProtocolFatal exit code 2."""
        invalid_bytes = b"\xff\xfe\xfa\n"
        with self.assertRaises(A.AdapterProtocolFatal) as ctx:
            self.engine.run_raw(invalid_bytes)
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn("invalid UTF-8", ctx.exception.stderr_excerpt)

    def test_truncated_multibyte_utf8_sequence(self):
        """Truncated 3-byte Chinese UTF-8 sequence must trigger AdapterProtocolFatal exit code 2."""
        # 0xE4 0xBD is the start of '你' (0xE4 0xBD 0xA0) missing the trailing byte
        truncated = b'{"v":1,"id":"trunc-utf8","op":"identify","hits":[{"url":"https://example.com/' + b"\xe4\xbd" + b'"}]}\n'
        with self.assertRaises(A.AdapterProtocolFatal) as ctx:
            self.engine.run_raw(truncated)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_overlong_utf8_encoding_raises_protocol_fatal(self):
        """Overlong 2-byte encoding of slash (0xC0 0xAF) must trigger AdapterProtocolFatal exit 2."""
        overlong = b'{"v":1,"id":"overlong","op":"identify","hits":[{"url":"https://example.com' + b"\xc0\xaf" + b'test"}]}\n'
        with self.assertRaises(A.AdapterProtocolFatal) as ctx:
            self.engine.run_raw(overlong)
        self.assertEqual(ctx.exception.exit_code, 2)


class TestAdversarialZeroBinaryIsolation(unittest.TestCase):
    """Stress testing that pure-Python identity adapter operates with zero binary dependencies."""

    def test_client_fallback_never_spawns_subprocess(self):
        """AdapterClient with fallback=True and binary_path=None must NEVER call subprocess.Popen."""
        with mock.patch("subprocess.Popen") as mock_popen:
            client = A.AdapterClient(fallback=True)
            self.assertTrue(client._use_python)
            
            # Execute batch through client
            reqs = [make_request("zero-bin-1"), make_request("zero-bin-2")]
            res = client.run(reqs)
            
            self.assertEqual(len(res.records), 2)
            self.assertTrue(res.records[0]["ok"])
            self.assertTrue(res.records[1]["ok"])
            mock_popen.assert_not_called()

    def test_client_with_nonexistent_binary_falls_back_without_spawning(self):
        """AdapterClient pointing to nonexistent /nonexistent/rt-identity falls back to Python."""
        with mock.patch("subprocess.Popen") as mock_popen:
            client = A.AdapterClient(binary_path="/nonexistent/rt-identity", fallback=True)
            self.assertTrue(client._use_python)
            
            res = client.run([make_request("zb-fallback")])
            self.assertEqual(len(res.records), 1)
            self.assertTrue(res.records[0]["ok"])
            mock_popen.assert_not_called()

    def test_subprocess_popen_disallowed_globally_during_engine_run(self):
        """When subprocess.Popen is poisoned, PythonIdentityEngine still runs flawlessly."""
        with mock.patch("subprocess.Popen", side_effect=RuntimeError("Subprocess execution is strictly forbidden")):
            engine = A.PythonIdentityEngine()
            res = engine.run([make_request("isolated-test")])
            self.assertEqual(len(res.records), 1)
            self.assertTrue(res.records[0]["ok"])


class TestAdversarialMalformedHitsAndExtremeDepth(unittest.TestCase):
    """Stress testing malformed hit types, non-string URLs, and extreme AST depth."""

    def setUp(self):
        self.engine = A.PythonIdentityEngine()

    def test_malformed_hit_element_not_dict(self):
        """Hit element that is a string, int, or list must emit malformed_json."""
        for bad_hit in ("invalid_string_hit", 12345, ["list_hit"]):
            with self.subTest(bad_hit=bad_hit):
                req = {"v": 1, "id": "req-bad-hit", "op": "identify", "hits": [bad_hit]}
                res = self.engine.run([req])
                self.assertEqual(len(res.records), 1)
                rec = res.records[0]
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["id"], "req-bad-hit")
                self.assertEqual(rec["error"]["code"], "malformed_json")

    def test_malformed_url_not_string(self):
        """Hit with non-string url (None, int, dict) must emit url_blocked_no_host."""
        for bad_url in (None, 9999, {"url": "nested"}):
            with self.subTest(bad_url=bad_url):
                req = {
                    "v": 1,
                    "id": "req-bad-url",
                    "op": "identify",
                    "hits": [{"url": bad_url}],
                }
                res = self.engine.run([req])
                self.assertEqual(len(res.records), 1)
                rec = res.records[0]
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["id"], "req-bad-url")
                self.assertEqual(rec["error"]["code"], "url_blocked_no_host")

    def test_hits_not_a_list(self):
        """Request with hits as a dict or scalar must emit malformed_json."""
        for bad_hits in ({"url": "https://example.com"}, "not_a_list", 123):
            with self.subTest(bad_hits=bad_hits):
                req = {"v": 1, "id": "req-bad-hits", "op": "identify", "hits": bad_hits}
                res = self.engine.run([req])
                self.assertEqual(len(res.records), 1)
                rec = res.records[0]
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["id"], "req-bad-hits")
                self.assertEqual(rec["error"]["code"], "malformed_json")

    def test_deep_recursion_exceeding_1000_levels_does_not_crash(self):
        """Payload with 1,000 nested layers must safely emit limit_depth."""
        deep = 1
        for _ in range(1000):
            deep = [deep]
        req = {"v": 1, "id": "req-deep-1000", "op": "identify", "hits": [], "deep": deep}
        res = self.engine.run([req])
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "req-deep-1000")
        self.assertEqual(rec["error"]["code"], "limit_depth")


if __name__ == "__main__":
    unittest.main()
