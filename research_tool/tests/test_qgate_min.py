"""Tests for ⑧ quality-gate minimal (P3 slice 4).

Golden differential: frozen input (⑤ inspect envelopes from slice-3 golden,
read-only) → gate decision envelope byte-identical to frozen golden;
double-run and dict-insertion-order determinism; envelope/error-frame
compliance incl. E_BUDGET_EXHAUSTED and E_LEASE_INVALID paths; stop
precedence; scope hygiene. Filesystem-pure.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import qgate_min as Q

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden_qgate"


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
            qreq = Q.request_from_inspect(env)
            got = Q.canonical_output_bytes(Q.run_gate(qreq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run_byte_identical(self):
        inp, out = golden_lines()
        for i, line in enumerate(inp):
            env = json.loads(line)
            qreq = Q.request_from_inspect(env)
            o1 = Q.canonical_output_bytes(Q.run_gate(dict(qreq)))
            o2 = Q.canonical_output_bytes(Q.run_gate(dict(qreq)))
            self.assertEqual(o1, o2)
            self.assertEqual(o1.decode().rstrip("\n"), out[i])

    def test_dict_insertion_order_does_not_matter(self):
        line = golden_lines()[0][0]
        env = json.loads(line)
        shuffled = {k: env[k] for k in reversed(list(env.keys()))}
        qreq = Q.request_from_inspect(shuffled)
        got = Q.canonical_output_bytes(Q.run_gate(qreq))
        self.assertEqual(got.decode().rstrip("\n"), golden_lines()[1][0])


# --------------------------------------------------------------------------- #
# 2. Verdict matrix (frozen decision tree, PROPOSED thresholds)
# --------------------------------------------------------------------------- #
class TestVerdictMatrix(unittest.TestCase):
    def base(self, line_index=0):
        line = golden_lines()[0][line_index]
        return Q.request_from_inspect(json.loads(line))

    def test_no_findings_stop_success_without_degrade(self):
        req = self.base(0)
        req["inspect_results"][0]["result"]["findings"] = []
        req["idempotency_key"] = Q.compute_idempotency_key(
            req["inspect_results"])
        env = Q.run_gate(req)
        self.assertEqual(env["result"]["verdict"], "STOP_SUCCESS")
        self.assertNotIn("degraded: low-priority findings remain",
                         env["result"]["reasons"])

    def test_low_only_stop_success_with_degrade_note(self):
        env = Q.run_gate(self.base(0))
        self.assertEqual(env["result"]["verdict"], "STOP_SUCCESS")
        self.assertIn("degraded: low-priority findings remain",
                       env["result"]["reasons"])

    def test_high_over_threshold_continue_to_targeted_research(self):
        req = self.base(6)  # line 6 carries the 1 high finding
        env = Q.run_gate(req)
        self.assertEqual(env["result"]["verdict"], "CONTINUE")
        self.assertIn("route to ⑥ targeted-research",
                      env["result"]["reasons"])

    def test_high_within_threshold_stop_success(self):
        req = self.base(6)
        req["thresholds"] = {"max_high_findings": 1, "max_total_findings": 10}
        env = Q.run_gate(req)
        self.assertEqual(env["result"]["verdict"], "STOP_SUCCESS")

    def test_total_over_threshold_continue(self):
        req = self.base(0)
        req["thresholds"] = {"max_high_findings": 0, "max_total_findings": 2}
        env = Q.run_gate(req)
        self.assertEqual(env["result"]["verdict"], "CONTINUE")
        self.assertIn("total findings exceed max_total_findings",
                      env["result"]["reasons"])

    def test_upstream_error_stop_error_precedence_over_budget(self):
        req = self.base(0)
        upstream = dict(req["inspect_results"][0])
        upstream["error"] = {"code": "inspect.E_INTERNAL",
                             "stage": "inspect",
                             "safe_message": "boom", "retryable": False}
        req["inspect_results"] = [upstream]
        req["idempotency_key"] = Q.compute_idempotency_key(
            req["inspect_results"])
        req["budget_lease"]["wall_s_max"] = 0  # exhausted too
        env = Q.run_gate(req)
        self.assertEqual(env["result"]["verdict"], "STOP_ERROR")
        self.assertTrue(any("upstream inspect failure" in r for r
                            in env["result"]["reasons"]))


# --------------------------------------------------------------------------- #
# 3. Envelope / error-frame compliance (E_BUDGET_EXHAUSTED + E_LEASE_INVALID)
# --------------------------------------------------------------------------- #
class TestEnvelopeCompliance(unittest.TestCase):
    def base(self, line_index=0):
        line = golden_lines()[0][line_index]
        return Q.request_from_inspect(json.loads(line))

    def assert_frame(self, request, code):
        env = Q.run_gate(request)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], code)
        self.assertEqual(env["error"]["stage"], "gate")
        self.assertIsInstance(env["error"]["safe_message"], str)
        self.assertIsInstance(env["error"]["retryable"], bool)

    def test_success_envelope_full_compliance(self):
        env = Q.run_gate(self.base(0))
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["stage"], "gate")
        self.assertEqual(len(env["idempotency_key"]), 64)
        self.assertEqual(env["result"]["verdict"], "STOP_SUCCESS")
        self.assertEqual(env["result"]["thresholds"],
                         {"max_high_findings": 0, "max_total_findings": 10})
        self.assertIsNone(env["error"])

    def test_lease_exhausted_budget_frame(self):
        req = self.base(0)
        req["budget_lease"]["wall_s_max"] = 0
        env = Q.run_gate(req)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], Q.E_BUDGET_EXHAUSTED)
        self.assertFalse(env["error"]["retryable"])

    def test_lease_invalid(self):
        req = self.base(0)
        del req["budget_lease"]["lease_id"]
        self.assert_frame(req, Q.E_LEASE_INVALID)

    def test_version_mismatch(self):
        req = self.base(0)
        req["v"] = 2
        self.assert_frame(req, Q.E_VERSION)

    def test_stage_mismatch(self):
        req = self.base(0)
        req["stage"] = "report"
        self.assert_frame(req, Q.E_SCHEMA)

    def test_thresholds_structure_frozen(self):
        req = self.base(0)
        req["thresholds"] = {"max_high_findings": 0}  # missing max_total
        self.assert_frame(req, Q.E_SCHEMA)

    def test_thresholds_negative_rejected(self):
        req = self.base(0)
        req["thresholds"] = {"max_high_findings": -1, "max_total_findings": 10}
        self.assert_frame(req, Q.E_SCHEMA)

    def test_idempotency_conflict(self):
        req = self.base(0)
        req["idempotency_key"] = "f" * 64
        self.assert_frame(req, Q.E_IDEMPOTENCY_CONFLICT)

    def test_error_envelope_has_no_partial_result(self):
        req = self.base(0)
        req["idempotency_key"] = "a" * 64
        env = Q.run_gate(req)
        self.assertIsNone(env["result"])
        self.assertNotIn("verdict", json.dumps(env))


# --------------------------------------------------------------------------- #
# 4. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/qgate_min.py").read_text(
            encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
