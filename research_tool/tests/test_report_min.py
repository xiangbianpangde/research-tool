"""Tests for ⑨ verified report minimal (P3 slice 5 / skeleton final segment).

Golden differential: frozen chain input (⑧ gate decision + ④ network
envelopes, read-only) → report envelope byte-identical to frozen golden;
double-run and dict-insertion-order determinism; envelope/error-frame
compliance; untraceable-claim disposition (drop + count, never silent);
verdict routing; scope hygiene. Filesystem-pure.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import report_min as R

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden_report"


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def golden_lines():
    inp = (GOLDEN / "input.jsonl").read_text(encoding="utf-8").splitlines()
    out = (GOLDEN / "output.jsonl").read_text(encoding="utf-8").splitlines()
    return inp, out


def chain_from_line(i):
    line = golden_lines()[0][i]
    chain = json.loads(line)
    return chain["gate_results"][0], chain["network_results"]


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
            chain = json.loads(line)
            rreq = R.request_from_chain(chain["gate_results"][0],
                                        chain["network_results"])
            got = R.canonical_output_bytes(R.run_report(rreq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run_byte_identical(self):
        inp, out = golden_lines()
        for i, line in enumerate(inp):
            chain = json.loads(line)
            rreq = R.request_from_chain(chain["gate_results"][0],
                                        chain["network_results"])
            o1 = R.canonical_output_bytes(R.run_report(dict(rreq)))
            o2 = R.canonical_output_bytes(R.run_report(dict(rreq)))
            self.assertEqual(o1, o2)
            self.assertEqual(o1.decode().rstrip("\n"), out[i])

    def test_dict_insertion_order_does_not_matter(self):
        chain = json.loads(golden_lines()[0][0])
        gate = dict(reversed(list(chain["gate_results"][0].items())))
        nets = [dict(reversed(list(n.items())))
                for n in reversed(chain["network_results"])]
        rreq = R.request_from_chain(gate, nets)
        got = R.canonical_output_bytes(R.run_report(rreq))
        self.assertEqual(got.decode().rstrip("\n"), golden_lines()[1][0])


# --------------------------------------------------------------------------- #
# 2. Verdict routing + claim traceability
# --------------------------------------------------------------------------- #
class TestVerdictRouting(unittest.TestCase):
    def test_stop_success_assembles_report_with_verbatim_notes(self):
        gate, nets = chain_from_line(0)
        env = R.run_report(R.request_from_chain(gate, nets))
        self.assertEqual(env["result"]["verdict"], "STOP_SUCCESS")
        report = env["result"]["report"]
        self.assertTrue(report["claims"])
        # degraded/STOP notes preserved verbatim from the gate decision
        gate_reasons = gate["result"]["reasons"]
        self.assertEqual(report["gate_notes"], gate_reasons)

    def test_stop_budget_assembles_with_note(self):
        gate, nets = chain_from_line(0)
        gate = json.loads(json.dumps(gate))
        gate["result"]["verdict"] = "STOP_BUDGET"
        gate["result"]["reasons"] = ["budget exhausted before quality met"]
        env = R.run_report(R.request_from_chain(gate, nets))
        self.assertEqual(env["result"]["verdict"], "STOP_BUDGET")
        self.assertTrue(env["result"]["report"]["claims"])
        self.assertIn("budget stop", env["result"]["note"])
        self.assertEqual(env["result"]["report"]["gate_notes"],
                         ["budget exhausted before quality met"])

    def test_continue_no_report(self):
        gate, nets = chain_from_line(6)  # CONTINUE line
        env = R.run_report(R.request_from_chain(gate, nets))
        self.assertEqual(env["result"]["verdict"], "CONTINUE")
        self.assertIsNone(env["result"]["report"])
        self.assertIn("⑥", env["result"]["note"])

    def test_stop_error_failure_bundle_note(self):
        gate, nets = chain_from_line(0)
        gate = json.loads(json.dumps(gate))
        gate["result"]["verdict"] = "STOP_ERROR"
        gate["result"]["reasons"] = ["upstream inspect failure"]
        env = R.run_report(R.request_from_chain(gate, nets))
        self.assertEqual(env["result"]["verdict"], "STOP_ERROR")
        self.assertIsNone(env["result"]["report"])
        self.assertIn("failure bundle", env["result"]["note"])

    def test_claims_carry_citations_traceability(self):
        gate, nets = chain_from_line(0)
        env = R.run_report(R.request_from_chain(gate, nets))
        for claim in env["result"]["report"]["claims"]:
            self.assertTrue(claim["citations"], claim["claim_id"])
            for cite in claim["citations"]:
                self.assertIn("source_id", cite)
                self.assertIn("locator", cite)
                self.assertIn("content_sha256", cite)

    def test_untraceable_claim_dropped_and_counted(self):
        gate, nets = chain_from_line(0)
        nets = json.loads(json.dumps(nets))
        nets[0]["result"]["nodes"].append({
            "node_id": "orphan:family",
            "kind": "source",
            "evidence_spans": [],  # no resolvable citation
        })
        env = R.run_report(R.request_from_chain(gate, nets))
        counts = env["result"]["counts"]
        self.assertEqual(counts["dropped_claims"], 1)
        claim_ids = {c["claim_id"] for c in
                     env["result"]["report"]["claims"]}
        self.assertNotIn("claim:orphan:family", claim_ids)
        self.assertIn({"claim_id": "claim:orphan:family",
                       "reason": "no_resolvable_citation",
                       "node_id": "orphan:family"},
                      env["result"]["dropped_claims"])


# --------------------------------------------------------------------------- #
# 3. Envelope / error-frame compliance
# --------------------------------------------------------------------------- #
class TestEnvelopeCompliance(unittest.TestCase):
    def base(self, line_index=0):
        gate, nets = chain_from_line(line_index)
        return R.request_from_chain(gate, nets)

    def assert_frame(self, request, code):
        env = R.run_report(request)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], code)
        self.assertEqual(env["error"]["stage"], "report")
        self.assertIsInstance(env["error"]["safe_message"], str)
        self.assertIsInstance(env["error"]["retryable"], bool)

    def test_success_envelope_full_compliance(self):
        env = R.run_report(self.base(0))
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["stage"], "report")
        self.assertEqual(len(env["idempotency_key"]), 64)
        self.assertIn("lease_id", env["budget_lease"])
        self.assertIsNone(env["error"])

    def test_version_mismatch(self):
        req = self.base(0)
        req["v"] = 2
        self.assert_frame(req, R.E_VERSION)

    def test_stage_mismatch(self):
        req = self.base(0)
        req["stage"] = "merge"
        self.assert_frame(req, R.E_SCHEMA)

    def test_missing_gate_results(self):
        req = self.base(0)
        del req["gate_results"]
        self.assert_frame(req, R.E_SCHEMA)

    def test_invalid_verdict_rejected(self):
        req = self.base(0)
        req["gate_results"][0]["result"]["verdict"] = "MAYBE"
        self.assert_frame(req, R.E_SCHEMA)

    def test_missing_network_results(self):
        req = self.base(0)
        del req["network_results"]
        self.assert_frame(req, R.E_SCHEMA)

    def test_idempotency_conflict(self):
        req = self.base(0)
        req["idempotency_key"] = "f" * 64
        self.assert_frame(req, R.E_IDEMPOTENCY_CONFLICT)

    def test_lease_invalid(self):
        req = self.base(0)
        del req["budget_lease"]["lease_id"]
        self.assert_frame(req, R.E_LEASE_INVALID)

    def test_error_envelope_has_no_partial_report(self):
        req = self.base(0)
        req["idempotency_key"] = "a" * 64
        env = R.run_report(req)
        self.assertIsNone(env["result"])
        self.assertNotIn("claims", json.dumps(env))


# --------------------------------------------------------------------------- #
# 4. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/report_min.py").read_text(
            encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
