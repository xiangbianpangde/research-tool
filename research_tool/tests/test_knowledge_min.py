"""Tests for ④ knowledge-network minimal (P3 slice 2).

Golden differential: frozen input (① collect envelopes) → output byte-identical
to the frozen golden; double-run determinism; envelope/error-frame compliance;
scope hygiene (core + ① collect unchanged). Filesystem-pure apart from reading
the frozen golden directory.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import knowledge_min as K

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden"


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def golden_lines():
    inp = (GOLDEN / "input.jsonl").read_text(encoding="utf-8").splitlines()
    out = (GOLDEN / "output.jsonl").read_text(encoding="utf-8").splitlines()
    return inp, out


# --------------------------------------------------------------------------- #
# 1. Golden differential + determinism
# --------------------------------------------------------------------------- #
class TestGoldenDifferential(unittest.TestCase):
    def test_sha_manifest_matches_frozen_files(self):
        sums = (GOLDEN / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        for entry in sums:
            sha, name = entry.split()
            self.assertEqual(sha256_file(GOLDEN / name), sha, name)

    def test_output_byte_identical_to_golden(self):
        inp, out = golden_lines()
        self.assertEqual(len(inp), len(out))
        for i, line in enumerate(inp):
            env = json.loads(line)
            nreq = K.request_from_collect(env)
            got = K.canonical_output_bytes(K.run_network(nreq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run_byte_identical(self):
        inp, out = golden_lines()
        for i, line in enumerate(inp):
            env = json.loads(line)
            nreq = K.request_from_collect(env)
            o1 = K.canonical_output_bytes(K.run_network(dict(nreq)))
            o2 = K.canonical_output_bytes(K.run_network(dict(nreq)))
            self.assertEqual(o1, o2)
            self.assertEqual(o1.decode().rstrip("\n"), out[i])

    def test_dict_insertion_order_does_not_matter(self):
        """Same input with different key insertion order ⇒ same output bytes
        (proves output never depends on dict iteration order)."""
        line = golden_lines()[0][0]
        env = json.loads(line)
        shuffled = {k: env[k] for k in reversed(list(env.keys()))}
        nreq = K.request_from_collect(shuffled)
        got = K.canonical_output_bytes(K.run_network(nreq))
        self.assertEqual(got.decode().rstrip("\n"),
                         golden_lines()[1][0])

    def test_multi_record_network_counts(self):
        """All 10 collect records in one batch: nodes = unique families,
        same_bytes edges only between distinct families sharing a hash."""
        inp, _ = golden_lines()
        records = [json.loads(line) for line in inp]
        nreq = {"v": 1, "run_id": "multi", "stage": "network",
                "request_id": "multi-1",
                "idempotency_key": K.compute_idempotency_key(records),
                "budget_lease": records[0]["budget_lease"],
                "collect_results": records}
        env = K.run_network(nreq)
        self.assertIsNone(env["error"])
        families = {n["node_id"] for n in env["result"]["nodes"]}
        self.assertEqual(len(env["result"]["nodes"]), len(families))
        for edge in env["result"]["edges"]:
            self.assertEqual(edge["type"], "same_bytes")
            self.assertNotEqual(edge["source"], edge["target"])
        self.assertEqual(env["result"]["counts"]["nodes"], len(families))
        self.assertEqual(env["result"]["counts"]["edges"],
                         len(env["result"]["edges"]))


# --------------------------------------------------------------------------- #
# 2. Envelope / error-frame compliance (contract §2/§4)
# --------------------------------------------------------------------------- #
class TestEnvelopeCompliance(unittest.TestCase):
    def base(self):
        line = golden_lines()[0][0]
        return K.request_from_collect(json.loads(line))

    def assert_frame(self, request, code):
        env = K.run_network(request)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], code)
        self.assertEqual(env["error"]["stage"], "network")
        self.assertIsInstance(env["error"]["safe_message"], str)
        self.assertIsInstance(env["error"]["retryable"], bool)

    def test_success_envelope_full_compliance(self):
        env = K.run_network(self.base())
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["stage"], "network")
        self.assertIsNotNone(env["idempotency_key"])
        self.assertIn("lease_id", env["budget_lease"])
        result = env["result"]
        for node in result["nodes"]:
            self.assertEqual(sorted(node.keys()),
                             ["evidence_spans", "kind", "node_id"])
            for span in node["evidence_spans"]:
                for field in ("source_id", "content_sha256", "locator",
                              "extractor_version", "round_id"):
                    self.assertIn(field, span)
        self.assertIsNone(env["error"])

    def test_version_mismatch(self):
        req = self.base()
        req["v"] = 2
        self.assert_frame(req, K.E_VERSION)

    def test_stage_mismatch(self):
        req = self.base()
        req["stage"] = "clean"
        self.assert_frame(req, K.E_SCHEMA)

    def test_missing_collect_results(self):
        req = self.base()
        del req["collect_results"]
        self.assert_frame(req, K.E_SCHEMA)

    def test_non_collect_record_rejected(self):
        req = self.base()
        bad = dict(req["collect_results"][0])
        bad["stage"] = "clean"
        req["collect_results"] = [bad]
        self.assert_frame(req, K.E_SCHEMA)

    def test_idempotency_conflict(self):
        req = self.base()
        req["idempotency_key"] = "f" * 64
        self.assert_frame(req, K.E_IDEMPOTENCY_CONFLICT)

    def test_lease_invalid(self):
        req = self.base()
        del req["budget_lease"]["lease_id"]
        self.assert_frame(req, K.E_LEASE_INVALID)

    def test_error_envelope_has_no_partial_result(self):
        req = self.base()
        req["idempotency_key"] = "a" * 64
        env = K.run_network(req)
        self.assertIsNone(env["result"])
        self.assertNotIn("nodes", json.dumps(env))


# --------------------------------------------------------------------------- #
# 3. Scope hygiene: no network imports
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/knowledge_min.py").read_text(
            encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
