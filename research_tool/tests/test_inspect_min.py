"""Tests for ⑤ inspect minimal (P3 slice 3).

Golden differential: frozen input (④ network envelopes from slice-2 golden,
read-only) → output byte-identical to frozen golden; double-run and
dict-insertion-order determinism; envelope/error-frame compliance; scope
hygiene. Filesystem-pure.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import inspect_min as I

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden_inspect"


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
            ireq = I.request_from_network(env)
            got = I.canonical_output_bytes(I.run_inspect(ireq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run_byte_identical(self):
        inp, out = golden_lines()
        for i, line in enumerate(inp):
            env = json.loads(line)
            ireq = I.request_from_network(env)
            o1 = I.canonical_output_bytes(I.run_inspect(dict(ireq)))
            o2 = I.canonical_output_bytes(I.run_inspect(dict(ireq)))
            self.assertEqual(o1, o2)
            self.assertEqual(o1.decode().rstrip("\n"), out[i])

    def test_dict_insertion_order_does_not_matter(self):
        line = golden_lines()[0][0]
        env = json.loads(line)
        shuffled = {k: env[k] for k in reversed(list(env.keys()))}
        ireq = I.request_from_network(shuffled)
        got = I.canonical_output_bytes(I.run_inspect(ireq))
        self.assertEqual(got.decode().rstrip("\n"), golden_lines()[1][0])

    def test_finding_sorting_and_shapes(self):
        inp, out = golden_lines()
        env = json.loads(I.canonical_output_bytes(
            I.run_inspect(I.request_from_network(json.loads(inp[6])))))
        findings = env["result"]["findings"]
        ranks = [I._PRIORITY_RANK[f["priority"]] for f in findings]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual([f["finding_id"] for f in findings],
                         sorted(f["finding_id"] for f in findings))
        for f in findings:
            self.assertIn(f["type"], ("contradiction", "gap"))
            self.assertIn(f["priority"], I._PRIORITY_RANK)
            self.assertIn(f["code"],
                          ("same_bytes_cross_family", "orphan_node",
                           "span_incomplete"))


# --------------------------------------------------------------------------- #
# 2. Envelope / error-frame compliance
# --------------------------------------------------------------------------- #
class TestEnvelopeCompliance(unittest.TestCase):
    def base(self):
        line = golden_lines()[0][0]
        return I.request_from_network(json.loads(line))

    def assert_frame(self, request, code):
        env = I.run_inspect(request)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], code)
        self.assertEqual(env["error"]["stage"], "inspect")
        self.assertIsInstance(env["error"]["safe_message"], str)
        self.assertIsInstance(env["error"]["retryable"], bool)

    def test_success_envelope_full_compliance(self):
        env = I.run_inspect(self.base())
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["stage"], "inspect")
        self.assertEqual(len(env["idempotency_key"]), 64)
        self.assertIn("lease_id", env["budget_lease"])
        self.assertIsNone(env["error"])
        result = env["result"]
        self.assertIn("total", result["counts"])
        self.assertEqual(result["counts"]["total"], len(result["findings"]))
        for entry in result["networks_inspected"]:
            self.assertIn("request_id", entry)
            self.assertEqual(len(entry["network_digest"]), 64)

    def test_version_mismatch(self):
        req = self.base()
        req["v"] = 2
        self.assert_frame(req, I.E_VERSION)

    def test_stage_mismatch(self):
        req = self.base()
        req["stage"] = "merge"
        self.assert_frame(req, I.E_SCHEMA)

    def test_missing_network_results(self):
        req = self.base()
        del req["network_results"]
        self.assert_frame(req, I.E_SCHEMA)

    def test_non_network_record_rejected(self):
        req = self.base()
        bad = dict(req["network_results"][0])
        bad["stage"] = "collect"
        req["network_results"] = [bad]
        self.assert_frame(req, I.E_SCHEMA)

    def test_idempotency_conflict(self):
        req = self.base()
        req["idempotency_key"] = "f" * 64
        self.assert_frame(req, I.E_IDEMPOTENCY_CONFLICT)

    def test_lease_invalid(self):
        req = self.base()
        del req["budget_lease"]["lease_id"]
        self.assert_frame(req, I.E_LEASE_INVALID)

    def test_error_envelope_has_no_partial_result(self):
        req = self.base()
        req["idempotency_key"] = "a" * 64
        env = I.run_inspect(req)
        self.assertIsNone(env["result"])
        self.assertNotIn("findings", json.dumps(env))


# --------------------------------------------------------------------------- #
# 3. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/inspect_min.py").read_text(
            encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
