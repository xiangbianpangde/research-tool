"""E2E chain tests — P3 mid-closeout (RT-RF-P3-E2E-MID-01).

Four classes:
  1. golden      — full five-stage chain → final report byte-identical to
                   golden_e2e; stage-boundary artifacts equal the accepted
                   per-stage golden chains (read-only)
  2. crash/resume— fault injected after every stage boundary → resume yields
                   a byte-identical final report, no partial artifacts, and
                   each stage executes at most once across both runs
  3. duplicate   — same input run twice on the same work dir → second run is
                   a zero-delta no-op (zero new executions, artifacts
                   unchanged)
  4. determinism — two fresh work dirs → byte-identical final report
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import unittest

from research_tool.nine_loop.rt_identity_adapter import AdapterClient
from research_tool.nine_loop import collect_stage as C
from research_tool.nine_loop import e2e

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden_e2e"
SCRATCH = pathlib.Path("/tmp/research_tool_scratch/RT-RF-P3-E2E-MID-01")


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def client():
    return AdapterClient(fallback=True)


def golden_lines():
    inp = (GOLDEN / "input.jsonl").read_text(encoding="utf-8").splitlines()
    out = (GOLDEN / "output.jsonl").read_text(encoding="utf-8").splitlines()
    return inp, out


class ChainTestCase(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, SCRATCH, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 1. E2E golden
# --------------------------------------------------------------------------- #
class TestE2EGolden(ChainTestCase):
    def test_sha_manifest_matches_frozen_files(self):
        sums = (GOLDEN / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        for entry in sums:
            sha, name = entry.split()
            self.assertEqual(sha256_file(GOLDEN / name), sha, name)

    def test_full_chain_matches_golden_final_report(self):
        inp, out = golden_lines()
        for i, line in enumerate(inp):
            request = json.loads(line)
            wd = SCRATCH / f"golden-{i}"
            env = e2e.E2EChain(client(), wd).run(request)
            self.assertIsNone(env["error"], line[:80])
            self.assertEqual(
                json.dumps(env, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                out[i],
                f"line {i}",
            )

    def test_stage_boundary_artifacts_equal_accepted_golden_chains(self):
        """Artifacts at each boundary equal the accepted per-stage golden
        chain lines (read-only references):
        ① collect  ← golden/input.jsonl (collect envelopes)
        ④ network  ← golden/output.jsonl
        ⑤ inspect  ← golden_inspect/output.jsonl
        ⑧ gate     ← golden_qgate/output.jsonl
        ⑨ report   ← golden_report/output.jsonl"""
        gc = (FIXTURES / "golden/input.jsonl").read_text()
        gn = (FIXTURES / "golden/output.jsonl").read_text()
        gi = (FIXTURES / "golden_inspect/output.jsonl").read_text()
        gg = (FIXTURES / "golden_qgate/output.jsonl").read_text()
        gr = (FIXTURES / "golden_report/output.jsonl").read_text()
        lc, ln, li, lg, lr = (x.splitlines() for x in (gc, gn, gi, gg, gr))
        for i, line in enumerate(golden_lines()[0]):
            request = json.loads(line)
            wd = SCRATCH / f"chain-{i}"
            chain = e2e.E2EChain(client(), wd)
            chain.run(request)
            art = wd / "artifacts"
            def canon(env):
                return json.dumps(
                    env, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                )
            self.assertEqual(
                canon(json.loads((art / "collect.json").read_text())),
                lc[i],
                f"collect {i}",
            )
            self.assertEqual(
                canon(json.loads((art / "network.json").read_text())),
                ln[i],
                f"network {i}",
            )
            self.assertEqual(
                canon(json.loads((art / "inspect.json").read_text())),
                li[i],
                f"inspect {i}",
            )
            self.assertEqual(
                canon(json.loads((art / "gate.json").read_text())),
                lg[i],
                f"gate {i}",
            )
            self.assertEqual(
                canon(json.loads((art / "report.json").read_text())),
                lr[i],
                f"report {i}",
            )


# --------------------------------------------------------------------------- #
# 2. Crash / resume
# --------------------------------------------------------------------------- #
class TestCrashResume(ChainTestCase):
    def _assert_report_matches(self, env, i):
        self.assertIsNone(env["error"])
        self.assertEqual(
            json.dumps(env, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
            golden_lines()[1][i],
        )

    def test_crash_after_each_boundary_then_resume(self):
        inp, out = golden_lines()
        # crash after every possible stage boundary (before report done)
        for crash_after in ("collect", "network", "inspect", "gate"):
            for i, line in enumerate(inp):
                request = json.loads(line)
                wd = SCRATCH / f"crash-{crash_after}-{i}"
                log: list[str] = []

                def hook(stage, _crash=crash_after):
                    if stage == _crash:
                        raise RuntimeError("injected crash")

                chain1 = e2e.E2EChain(client(), wd, fault_hook=hook, execution_log=log)
                with self.assertRaises(RuntimeError):
                    chain1.run(request)
                # no partial artifacts: only fully committed stages present
                state = json.loads((wd / "state.json").read_text())
                art = wd / "artifacts"
                for s in state["done"]:
                    self.assertTrue((art / f"{s}.json").exists(), s)
                leftovers = [p.name for p in art.iterdir() if p.name.endswith(".tmp-")]
                self.assertEqual(leftovers, [])

                log2: list[str] = []
                chain2 = e2e.E2EChain(client(), wd, execution_log=log2)
                env = chain2.run_resume(request)
                self._assert_report_matches(env, i)
                # each stage executes at most once across both runs
                all_log = log + log2
                for s in e2e.STAGES:
                    self.assertLessEqual(all_log.count(s), 1, s)

    def test_resume_different_input_key_rejected(self):
        request = json.loads(golden_lines()[0][0])
        wd = SCRATCH / "key-conflict"
        e2e.E2EChain(client(), wd).run(request)
        other = json.loads(json.dumps(request))
        other["request_id"] = "different"
        other["seeds"][0]["url"] = "http://example.com/other"
        other["idempotency_key"] = C.compute_idempotency_key(
            {"seeds": other["seeds"], "crawl_policy": {}}
        )
        with self.assertRaises(e2e.E2EFault) as ctx:
            e2e.E2EChain(client(), wd).run_resume(other)
        self.assertEqual(ctx.exception.code, e2e.E_IDEMPOTENCY_CONFLICT)


# --------------------------------------------------------------------------- #
# 3. Duplicate delta
# --------------------------------------------------------------------------- #
class TestDuplicateDelta(ChainTestCase):
    def test_second_run_zero_delta_artifacts_unchanged(self):
        request = json.loads(golden_lines()[0][0])
        wd = SCRATCH / "dup"
        log1: list[str] = []
        chain1 = e2e.E2EChain(client(), wd, execution_log=log1)
        env1 = chain1.run(request)
        before = {p.name: sha256_file(p) for p in (wd / "artifacts").iterdir()}
        log2: list[str] = []
        chain2 = e2e.E2EChain(client(), wd, execution_log=log2)
        env2 = chain2.run_resume(request)
        after = {p.name: sha256_file(p) for p in (wd / "artifacts").iterdir()}
        self.assertEqual(env1, env2)
        self.assertEqual(log2, [])  # zero new executions
        self.assertEqual(before, after)  # artifacts unchanged


# --------------------------------------------------------------------------- #
# 4. Determinism
# --------------------------------------------------------------------------- #
class TestDeterminism(ChainTestCase):
    def test_two_fresh_chains_byte_identical(self):
        inp, _ = golden_lines()
        for i, line in enumerate(inp):
            request = json.loads(line)
            wd1 = SCRATCH / f"det-{i}-a"
            wd2 = SCRATCH / f"det-{i}-b"
            env1 = e2e.E2EChain(client(), wd1).run(request)
            env2 = e2e.E2EChain(client(), wd2).run(request)
            self.assertEqual(
                json.dumps(env1, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                json.dumps(env2, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                f"line {i}",
            )


# --------------------------------------------------------------------------- #
# 5. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_imports(self):
        src = (pathlib.Path(__file__).resolve().parent.parent / "nine_loop/e2e.py").read_text(
            encoding="utf-8"
        )
        for banned in ("socket", "urllib", "http.client", "requests", "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
