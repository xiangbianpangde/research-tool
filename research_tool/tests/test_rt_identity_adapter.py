"""Tests for rt_identity_adapter — task RT-RF-P2-ADAPTER-IMPL-01.

Covers design §8 matrix: differential golden against the pinned engine,
protocol matrix, property invariants, fault injection (fake children via
``python -c``), atomic write + idempotency, security regression and the
healthy orchestration path. Disk-needing tests use ONLY the task scratch
directory and self-clean. No network, no third-party imports.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
import time
import unittest
from unittest import mock

from research_tool.nine_loop import rt_identity_adapter as A

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SCRATCH = pathlib.Path("/tmp/research_tool_scratch/RT-RF-P2-ADAPTER-IMPL-01")

POST_PARSE = {"limit_depth", "limit_field", "limit_count"}
PRE_PARSE = {"limit_line", "malformed_json"}


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def real_client(deadline_s: float = 30.0) -> A.AdapterClient:
    return A.AdapterClient(deadline_s=deadline_s, fallback=True)


def make_request(
    req_id="req-1",
    op="identify",
    n_hits=1,
    url_prefix="http://example.com/u",
    extra=None,
):
    hits = []
    for i in range(n_hits):
        hits.append(
            {
                "url": f"{url_prefix}/{i}",
                "title": "",
                "snippet": "",
                "source_engine": "web",
                "audit_engine": "web",
                "rank": i,
                "query_id": "q",
                "content": None,
            }
        )
    req = {"v": 1, "id": req_id, "op": op, "hits": hits}
    if extra:
        req.update(extra)
    return req


def encode(objs) -> bytes:
    return A.AdapterClient.encode_requests(objs)


class ScratchTestCase(unittest.TestCase):
    """Task-scratch backed tests; the directory self-cleans."""

    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, SCRATCH, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 1. Binary pin (design §3.4)
# --------------------------------------------------------------------------- #
class TestBinaryPin(unittest.TestCase):
    def test_mismatch_refuses_before_spawn(self):
        exe = sys.executable
        with self.assertRaises(A.AdapterBinaryMismatch):
            A.AdapterClient(exe, "0" * 64, fallback=False)

    def test_pinned_binary_constructs(self):
        exe = sys.executable
        client = A.AdapterClient(exe, sha256_file(exe))
        self.assertEqual(client.expected_sha256, sha256_file(exe))


# --------------------------------------------------------------------------- #
# 2. Differential golden: 82-ID corpus subset vs the REAL child (design §8.1)
# --------------------------------------------------------------------------- #
class TestDifferentialGolden(unittest.TestCase):
    GOLDEN_IDS = [
        "fx-alias-two-engines",
        "fx-identical-twice",
        "fx-arxiv-abs-vs-pdf-alias",
        "fx-canonical-same-http",
        "fx-github-fork",
        "fx-doi-prefix-distinct",
        "fx-same-bytes-two-doi",
        "fx-null-vs-hash-distinct",
        "fx-github-git",
        "fx-github-nogit",
    ]

    def _corpus_requests(self):
        cases = {}
        for name in ("oracle.v1.jsonl", "oracle.v1.1.addendum.jsonl"):
            p = FIXTURES / name
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                c = json.loads(line)
                cases[c["id"]] = c["request"]
        return [cases[i] for i in self.GOLDEN_IDS if i in cases]

    def test_adapter_matches_direct_child_drive_byte_for_byte(self):
        requests = self._corpus_requests()
        self.assertGreaterEqual(len(requests), 8)
        payload = encode(requests)
        direct_engine = A.PythonIdentityEngine()
        direct_res = direct_engine.run_raw(payload)
        self.assertEqual(direct_res.exit_code, 0)
        res = real_client().run_raw(payload)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stdout_bytes, direct_res.stdout_bytes)
        self.assertEqual(len(res.records), len(requests))

    def test_golden_records_are_contract_shaped(self):
        requests = self._corpus_requests()[:6]
        res = real_client().run(requests)
        self.assertEqual(len(res.records), len(requests))
        # every record ok with non-empty identities for these fixtures
        for rec in res.records:
            self.assertEqual(rec["v"], 1)
            self.assertTrue(rec["ok"])
            self.assertTrue(rec["identities"])


# --------------------------------------------------------------------------- #
# 3. Protocol matrix (design §8.2, §5 table) — real child
# --------------------------------------------------------------------------- #
class TestProtocolMatrix(unittest.TestCase):
    def test_empty_lines_skipped(self):
        payload = b"\n\n" + encode([make_request("e1")])
        res = real_client().run_raw(payload)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res.records), 1)
        self.assertTrue(res.records[0]["ok"])

    def test_malformed_json_record(self):
        payload = b'{"v":1,"id":"m","op":"identify","hits":[' + b"\n"
        res = real_client().run_raw(payload)
        self.assertEqual(len(res.records), 1)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertIsNone(rec["id"])
        self.assertEqual(rec["error"]["code"], "malformed_json")

    def test_depth_over_carries_request_id(self):
        val = 0
        for _ in range(9):
            val = [val]
        payload = encode([{"v": 1, "id": "d1", "op": "identify", "hits": [], "nested": val}])
        res = real_client().run_raw(payload)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "d1")
        self.assertEqual(rec["error"]["code"], "limit_depth")

    def test_field_over_carries_request_id(self):
        req = make_request("f1")
        req["hits"][0]["url"] = "a" * 65537
        res = real_client().run_raw(encode([req]))
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "f1")
        self.assertEqual(rec["error"]["code"], "limit_field")

    def test_hits_over_carries_request_id(self):
        res = real_client().run([make_request("h1", n_hits=1001)])
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["id"], "h1")
        self.assertEqual(rec["error"]["code"], "limit_count")

    def test_line_over_1mib_is_pre_parse_null(self):
        payload = b"x" * 1048577 + b"\n"
        res = real_client().run_raw(payload)
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertIsNone(rec["id"])
        self.assertEqual(rec["error"]["code"], "limit_line")

    def test_no_lf_8mib_is_protocol_fatal(self):
        payload = b"z" * (8 * 1024 * 1024 + 1)
        with self.assertRaises(A.AdapterProtocolFatal) as ctx:
            real_client().run_raw(payload)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_invalid_utf8_is_protocol_fatal(self):
        with self.assertRaises(A.AdapterProtocolFatal):
            real_client().run_raw(b"\xff\xfe\xfa\n")

    def test_version_too_new_is_protocol_fatal(self):
        with self.assertRaises(A.AdapterProtocolFatal):
            real_client().run([make_request("v9", extra={"v": 2})])

    def test_record_cap_10001(self):
        reqs = [
            make_request(f"c{i:05d}", url_prefix="http://example.com/c") for i in range(10001)
        ]
        res = real_client(deadline_s=60.0).run(reqs)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res.records), 10001)
        self.assertTrue(all(r["ok"] for r in res.records[:10000]))
        last = res.records[10000]
        self.assertFalse(last["ok"])
        self.assertIsNone(last["id"])
        self.assertEqual(last["error"]["code"], "limit_count")

    def test_unknown_op_record(self):
        res = real_client().run([make_request("u1", op="frobnicate")])
        rec = res.records[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"]["code"], "unknown_op")


# --------------------------------------------------------------------------- #
# 4. Property invariants (design §8.3) — deterministic corpus
# --------------------------------------------------------------------------- #
class TestPropertyInvariants(unittest.TestCase):
    def test_batch_invariants_hold(self):
        reqs = []
        for i in range(24):
            if i % 4 == 0:
                reqs.append(make_request(f"p{i}", n_hits=2))
            elif i % 4 == 1:
                reqs.append(make_request(f"p{i}", op="frobnicate"))
            elif i % 4 == 2:
                r = make_request(f"p{i}")
                r["hits"][0]["url"] = "a" * 65537
                reqs.append(r)
            else:
                reqs.append(make_request(f"p{i}", url_prefix="https://example.org/p"))
        res = real_client().run(reqs)
        self.assertEqual(len(res.records), len(reqs))
        for rec in res.records:
            self.assertEqual(rec["v"], 1)
            self.assertIs(rec["id"], rec["id"])  # key present
            self.assertIs(type(rec["ok"]), bool)
            if rec["ok"]:
                for ident in rec["identities"]:
                    self.assertIn(ident["decision"], A._KNOWN_DECISIONS)
            else:
                code = rec["error"]["code"]
                if code in POST_PARSE:
                    self.assertIsInstance(rec["id"], str)
                if code in PRE_PARSE:
                    self.assertIsNone(rec["id"])
        # json-safe: full records re-serialize deterministically
        blob = json.dumps(res.records, sort_keys=True, ensure_ascii=False)
        self.assertIn('"v": 1', blob)


# --------------------------------------------------------------------------- #
# 5. Fault injection with fake children (design §8.4)
# --------------------------------------------------------------------------- #
class TestFaultInjection(unittest.TestCase):
    def fake_client(self, code: str, deadline_s: float = 5.0) -> A.AdapterClient:
        exe = sys.executable
        return A.AdapterClient(
            exe,
            sha256_file(exe),
            deadline_s=deadline_s,
            command=[exe, "-c", code],
            fallback=False,
        )

    def test_exit3_is_child_internal(self):
        client = self.fake_client("import sys; sys.exit(3)")
        with self.assertRaises(A.AdapterChildInternal) as ctx:
            client.run([make_request()])
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_exit2_is_protocol_fatal(self):
        client = self.fake_client("import sys; sys.exit(2)")
        with self.assertRaises(A.AdapterProtocolFatal):
            client.run([make_request()])

    def test_early_eof_is_contract_violation(self):
        client = self.fake_client("pass")  # exit 0, no output
        with self.assertRaises(A.AdapterContractViolation):
            client.run([make_request()])

    def test_record_count_mismatch_is_contract_violation(self):
        code = (
            "import json, sys\n"
            "r = {'v': 1, 'id': None, 'ok': True, 'identities': []}\n"
            "sys.stdout.write(json.dumps(r) + '\\n' + json.dumps(r) + '\\n')\n"
        )
        client = self.fake_client(code)
        with self.assertRaises(A.AdapterContractViolation):
            client.run([make_request()])  # 1 request, 2 records

    def test_stdout_noise_is_contract_violation(self):
        client = self.fake_client("import sys; sys.stdout.write('noise\\n')")
        with self.assertRaises(A.AdapterContractViolation) as ctx:
            client.run([make_request()])
        self.assertIn("not valid JSON", ctx.exception.reason)

    def test_timeout_kills_child(self):
        client = self.fake_client("import time; time.sleep(30)", deadline_s=0.5)
        start = time.monotonic()
        with self.assertRaises(A.AdapterTimeout):
            client.run([make_request()])
        self.assertLess(time.monotonic() - start, 10.0)

    def test_fault_messages_do_not_leak_payloads(self):
        client = self.fake_client("import time; time.sleep(30)", deadline_s=0.5)
        req = make_request("leak", url_prefix="http://example.com/SUPERSECRET")
        with self.assertRaises(A.AdapterTimeout) as ctx:
            client.run([req])
        self.assertNotIn("SUPERSECRET", str(ctx.exception))


# --------------------------------------------------------------------------- #
# 6. Atomic write + idempotency (design §4/§8.5)
# --------------------------------------------------------------------------- #
class TestAtomicWriteAndIdempotency(ScratchTestCase):
    def test_atomic_write_roundtrip_and_no_tmp_leftover(self):
        target = SCRATCH / "result.json"
        A.write_result_atomic(target, b'{"ok": true}')
        self.assertEqual(target.read_bytes(), b'{"ok": true}')
        self.assertEqual([p.name for p in SCRATCH.iterdir()], ["result.json"])

    def test_failed_replace_cleans_tmp_and_keeps_target(self):
        target = SCRATCH / "keep.json"
        target.write_bytes(b"OLD")
        with mock.patch("os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                A.write_result_atomic(target, b"NEW")
        self.assertEqual(target.read_bytes(), b"OLD")
        self.assertEqual([p.name for p in SCRATCH.iterdir()], ["keep.json"])

    def test_idempotency_key_stable_and_distinct(self):
        k1 = A.idempotency_key(b"payload-a")
        k2 = A.idempotency_key(b"payload-a")
        k3 = A.idempotency_key(b"payload-b")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertEqual(len(k1), 64)
        int(k1, 16)  # hex


# --------------------------------------------------------------------------- #
# 7. Security regression (design §8.6)
# --------------------------------------------------------------------------- #
class TestSecurityRegression(ScratchTestCase):
    def test_child_gets_empty_environment(self):
        env_file = SCRATCH / "env.json"
        code = (
            "import json, os\n"
            f"open({str(env_file)!r}, 'w').write("
            "json.dumps(dict(os.environ)))\n"
        )
        client = A.AdapterClient(
            sys.executable,
            sha256_file(sys.executable),
            command=[sys.executable, "-c", code],
            fallback=False,
        )
        with self.assertRaises(A.AdapterContractViolation):
            client.run([make_request()])  # no stdout records; env file written
        env = json.loads(env_file.read_text())
        # The adapter passes env={} faithfully; macOS/libc may inject its own
        # platform keys. The security property: NO caller environment is
        # forwarded — no PATH/HOME, no secret-shaped keys.
        platform_injected = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
        self.assertTrue(set(env) <= platform_injected, env)
        for key in env:
            self.assertNotIn("PATH", key)
            for secret_marker in ("SECRET", "TOKEN", "KEY", "PASSWORD"):
                self.assertNotIn(secret_marker, key)

    def test_adapter_imports_no_network_modules(self):
        src = (
            pathlib.Path(__file__).resolve().parent.parent / "nine_loop/rt_identity_adapter.py"
        ).read_text(encoding="utf-8")
        for banned in ("requests", "httpx", "aiohttp", "urllib.request"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


# --------------------------------------------------------------------------- #
# 8. Healthy orchestration (design §8 orchestration row)
# --------------------------------------------------------------------------- #
class TestHealthyOrchestration(ScratchTestCase):
    def test_three_request_batch_end_to_end(self):
        reqs = [
            make_request(f"ok{i}", url_prefix="http://example.com/ok") for i in range(3)
        ]
        client = real_client()
        res = client.run(reqs)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res), 3)
        for rec in res.records:
            self.assertTrue(rec["ok"])
            self.assertTrue(rec["identities"])
            self.assertEqual(rec["identities"][0]["decision"], "retain")
        # atomic persistence through the adapter helper
        target = SCRATCH / "batch-result.json"
        blob = json.dumps(res.records, sort_keys=True, ensure_ascii=False).encode("utf-8")
        A.write_result_atomic(target, blob)
        self.assertEqual(json.loads(target.read_bytes()), res.records)

    def test_encode_is_deterministic(self):
        reqs = [make_request("d1"), make_request("d2")]
        self.assertEqual(
            A.AdapterClient.encode_requests(reqs), A.AdapterClient.encode_requests(reqs)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
