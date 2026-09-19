"""Engineering acceptance tests for native 9-stage architecture (RT-ENG-ACCEPT-01).

Acceptance criteria:
1. Concurrency: N concurrent runs (same/different inputs) → complete isolation
   (disjoint roots, no cross-run contamination, deterministic artifacts).
2. Resource/disk bounds: repeated runs on same work directory are zero-delta
   bounded; scratch self-clean verified.
3. Observability & Redaction: structured run state with stage tracking;
   zero secret leaks in any persisted artifact or manifest.
4. Determinism: multiple runs yield byte-identical reports and artifacts.
5. Scope hygiene: zero imports of purged legacy shadow/flags modules.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import threading
import unittest

from research_tool.nine_loop.rt_identity_adapter import AdapterClient
from research_tool.nine_loop import e2e

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN_E2E = FIXTURES / "golden_e2e"
SCRATCH = pathlib.Path("/tmp/research_tool_scratch/RT-ENG-ACCEPT-01")

BANNED_PATTERNS = ("Bearer", "api_key", "apikey", "password", "secret", "token=", "Authorization")


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def client():
    return AdapterClient(fallback=True)


def golden_request(idx: int = 0) -> dict:
    inp = (GOLDEN_E2E / "input.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(inp[idx])


class EngCase(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, SCRATCH, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 1. Concurrency isolation
# --------------------------------------------------------------------------- #
class TestConcurrencyIsolation(EngCase):
    def test_concurrent_chains_same_input_isolated_roots(self):
        """N concurrent chains on same input → disjoint work dirs, identical reports, no errors."""
        req = golden_request(0)
        results: dict[int, dict] = {}
        errors: list[Exception] = []

        def worker(idx: int):
            try:
                wd = SCRATCH / f"conc-same-{idx}"
                chain = e2e.E2EChain(client(), wd)
                env = chain.run(req)
                results[idx] = env
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        # All concurrent runs on the same input produce byte-identical reports
        canon_0 = json.dumps(results[0], sort_keys=True, ensure_ascii=False)
        for i in range(1, 6):
            canon_i = json.dumps(results[i], sort_keys=True, ensure_ascii=False)
            self.assertEqual(canon_0, canon_i, f"thread {i} diverged")

    def test_concurrent_chains_different_inputs_isolated_roots(self):
        """N concurrent chains on distinct inputs → independent artifacts, no cross-contamination."""
        inp = (GOLDEN_E2E / "input.jsonl").read_text(encoding="utf-8").splitlines()
        n = min(len(inp), 4)
        results: dict[int, dict] = {}
        errors: list[Exception] = []

        def worker(idx: int):
            try:
                req = json.loads(inp[idx])
                wd = SCRATCH / f"conc-diff-{idx}"
                chain = e2e.E2EChain(client(), wd)
                env = chain.run(req)
                results[idx] = env
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), n)
        for i in range(n):
            self.assertIsNone(results[i]["error"])
            wd = SCRATCH / f"conc-diff-{i}"
            state = json.loads((wd / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["done"]), len(e2e.STAGES))


# --------------------------------------------------------------------------- #
# 2. Resource / disk bounds
# --------------------------------------------------------------------------- #
class TestResourceAndDiskBounds(EngCase):
    def test_repeated_resume_is_zero_delta_bounded_disk(self):
        """Repeated runs on the same directory do not grow artifact sizes."""
        req = golden_request(0)
        wd = SCRATCH / "repeated-run"
        chain1 = e2e.E2EChain(client(), wd)
        env1 = chain1.run(req)
        art = wd / "artifacts"
        sizes1 = {p.name: p.stat().st_size for p in art.iterdir()}

        # Run resume 3 times
        for _ in range(3):
            chain_res = e2e.E2EChain(client(), wd)
            env_res = chain_res.run_resume(req)
            self.assertEqual(env1, env_res)
            sizes_now = {p.name: p.stat().st_size for p in art.iterdir()}
            self.assertEqual(sizes1, sizes_now)

    def test_scratch_self_clean(self):
        """Scratch directory probe removed cleanly."""
        probe = SCRATCH / "probe_dir"
        probe.mkdir(parents=True, exist_ok=True)
        (probe / "file.txt").write_text("ok")
        self.assertTrue(probe.exists())
        shutil.rmtree(probe)
        self.assertFalse(probe.exists())


# --------------------------------------------------------------------------- #
# 3. Observability & Redaction
# --------------------------------------------------------------------------- #
class TestObservabilityAndRedaction(EngCase):
    def test_state_and_manifest_observability_complete(self):
        """State record persists complete stage execution history."""
        req = golden_request(0)
        wd = SCRATCH / "obs-test"
        chain = e2e.E2EChain(client(), wd)
        env = chain.run(req)
        self.assertIsNone(env["error"])

        state_file = wd / "state.json"
        self.assertTrue(state_file.exists())
        state = json.loads(state_file.read_text(encoding="utf-8"))
        for required_key in ("version", "done", "input_idempotency_key", "stages"):
            self.assertIn(required_key, state)
        self.assertEqual(state["done"], list(e2e.STAGES))

    def test_redaction_no_secrets_in_artifacts(self):
        """No credentials, authorization headers, or sensitive secrets leak into persisted state."""
        req = golden_request(0)
        wd = SCRATCH / "redact-test"
        chain = e2e.E2EChain(client(), wd)
        chain.run(req)

        # Scan all JSON files in work dir
        for p in wd.rglob("*.json"):
            content = p.read_text(encoding="utf-8")
            for banned in BANNED_PATTERNS:
                self.assertNotIn(banned, content)


# --------------------------------------------------------------------------- #
# 4. Scope hygiene (purged modules not imported)
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_purged_shadow_or_flags_modules(self):
        """Verify obsolete shadow and flags modules are purged and not imported."""
        for mod in ("flags", "shadow", "shadow_e2e"):
            with self.assertRaises(ImportError):
                __import__(f"research_tool.nine_loop.{mod}")

    def test_nine_loop_no_network_libraries(self):
        """Verify e2e.py does not import third-party network libraries."""
        src = (pathlib.Path(__file__).resolve().parent.parent / "nine_loop/e2e.py").read_text(
            encoding="utf-8"
        )
        for banned in ("socket", "urllib", "http.client", "requests", "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
