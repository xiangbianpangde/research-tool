"""Tests for ⑥ targeted-research + ⑦ merge (P3 slice 6, RT-RF-P3-LOOP-MIN-01).

Four golden classes:
  1. ⑥ golden — ⑤ gap findings → targeted requests byte-identical
  2. ⑦ golden — ⑥ requests + frozen responses → merged graph byte-identical
  3. ⑦ idempotent — same responses merged twice → zero duplicate delta
  4. Determinism — double-run + insertion-order

Plus: ⑥ zero-network syscall static assertion, error frames, scope hygiene.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import targeted_min as T
from research_tool.nine_loop import merge_min as M

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN_T = FIXTURES / "golden_loop_t"
GOLDEN_M = FIXTURES / "golden_loop_m"


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def golden_lines(base):
    inp = (base / "input.jsonl").read_text(encoding="utf-8").splitlines()
    out = (base / "output.jsonl").read_text(encoding="utf-8").splitlines()
    return inp, out


# --------------------------------------------------------------------------- #
# 1. ⑥ golden (gap findings -> targeted requests)
# --------------------------------------------------------------------------- #
class TestTargetedGolden(unittest.TestCase):
    def test_sha_manifest_t(self):
        sums = (GOLDEN_T / "SHA256SUMS").read_text().splitlines()
        for entry in sums:
            s, n = entry.split()
            self.assertEqual(sha256_file(GOLDEN_T / n), s, n)

    def test_requests_byte_identical(self):
        inp, out = golden_lines(GOLDEN_T)
        for i, line in enumerate(inp):
            env = json.loads(line)
            treq = T.request_from_inspect(env)
            got = T.canonical_output_bytes(T.run_targeted(treq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run(self):
        inp, _ = golden_lines(GOLDEN_T)
        for i, line in enumerate(inp):
            env = json.loads(line)
            treq = T.request_from_inspect(env)
            a = T.canonical_output_bytes(T.run_targeted(dict(treq)))
            b = T.canonical_output_bytes(T.run_targeted(dict(treq)))
            self.assertEqual(a, b)

    def test_no_network_syscall(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/targeted_min.py").read_text(encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio", "subprocess"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)

    def test_error_frames(self):
        env = json.loads(golden_lines(GOLDEN_T)[0][0])
        req = T.request_from_inspect(env)
        req["v"] = 2
        got = T.run_targeted(req)
        self.assertIsNone(got["result"])
        self.assertEqual(got["error"]["code"], T.E_VERSION)


# --------------------------------------------------------------------------- #
# 2. ⑦ golden (merge with frozen responses)
# --------------------------------------------------------------------------- #
class TestMergeGolden(unittest.TestCase):
    def test_sha_manifest_m(self):
        sums = (GOLDEN_M / "SHA256SUMS").read_text().splitlines()
        for entry in sums:
            s, n = entry.split()
            self.assertEqual(sha256_file(GOLDEN_M / n), s, n)

    def test_merge_graph_byte_identical(self):
        inp, out = golden_lines(GOLDEN_M)
        for i, line in enumerate(inp):
            chain = json.loads(line)
            mreq = M.request_from_chain(chain["network_results"][0],
                                        chain["responses"])
            got = M.canonical_output_bytes(M.run_merge(mreq))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run(self):
        inp, _ = golden_lines(GOLDEN_M)
        for i, line in enumerate(inp):
            chain = json.loads(line)
            mreq = M.request_from_chain(chain["network_results"][0],
                                        chain["responses"])
            a = M.canonical_output_bytes(M.run_merge(dict(mreq)))
            b = M.canonical_output_bytes(M.run_merge(dict(mreq)))
            self.assertEqual(a, b)

    def test_insertion_order(self):
        line = golden_lines(GOLDEN_M)[0][0]
        chain = json.loads(line)
        net = dict(reversed(list(chain["network_results"][0].items())))
        resp = [dict(reversed(list(r.items()))) for r in chain["responses"]]
        mreq = M.request_from_chain(net, resp)
        got = M.canonical_output_bytes(M.run_merge(mreq))
        self.assertEqual(got.decode().rstrip("\n"), golden_lines(GOLDEN_M)[1][0])

    def test_error_frames(self):
        chain = json.loads(golden_lines(GOLDEN_M)[0][0])
        mreq = M.request_from_chain(chain["network_results"][0],
                                    chain["responses"])
        mreq["v"] = 2
        got = M.run_merge(mreq)
        self.assertIsNone(got["result"])
        self.assertEqual(got["error"]["code"], M.E_VERSION)


# --------------------------------------------------------------------------- #
# 3. ⑦ idempotent merge (zero duplicate delta)
# --------------------------------------------------------------------------- #
class TestMergeIdempotent(unittest.TestCase):
    def test_second_merge_skips_all(self):
        chain = json.loads(golden_lines(GOLDEN_M)[0][0])
        net, resp = chain["network_results"][0], chain["responses"]
        mreq = M.request_from_chain(net, resp)
        first = M.run_merge(mreq)
        # build the updated network envelope with the merged graph
        merged_net = json.loads(json.dumps(net))
        merged_net["result"] = first["result"]["graph"]
        mreq2 = M.request_from_chain(merged_net, resp)
        second = M.run_merge(mreq2)
        self.assertEqual(second["result"]["counts"]["skipped"],
                         len(resp[0]["facts"]))
        self.assertEqual(second["result"]["counts"]["new_facts"], 0)
        # graph latest_digest unchanged
        self.assertEqual(second["result"]["latest_digest"],
                         first["result"]["latest_digest"])

    def test_cas_conflict(self):
        chain = json.loads(golden_lines(GOLDEN_M)[0][0])
        net, resp = chain["network_results"][0], chain["responses"]
        mreq = M.request_from_chain(net, resp, prev_digest="0" * 64)
        got = M.run_merge(mreq)
        self.assertIsNone(got["result"])
        self.assertEqual(got["error"]["code"], M.E_CAS_CONFLICT)


# --------------------------------------------------------------------------- #
# 4. Determinism
# --------------------------------------------------------------------------- #
class TestDeterminism(unittest.TestCase):
    def test_two_fresh_merges_byte_identical(self):
        line = json.loads(golden_lines(GOLDEN_M)[0][0])
        net, resp = line["network_results"][0], line["responses"]
        a = M.canonical_output_bytes(M.run_merge(
            M.request_from_chain(dict(net), [dict(r) for r in resp])))
        b = M.canonical_output_bytes(M.run_merge(
            M.request_from_chain(dict(net), [dict(r) for r in resp])))
        self.assertEqual(a, b)


# --------------------------------------------------------------------------- #
# 5. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports_merge(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/merge_min.py").read_text(encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio", "subprocess"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
