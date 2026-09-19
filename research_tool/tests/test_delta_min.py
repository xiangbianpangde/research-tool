"""Tests for ② clean + ③ extract delta minimal (P3 slice 7, DELTA-MIN-01).

Golden differential (records→cleaned; cleaned→facts), delta idempotency
(content_sha256 skip → zero new output on re-run), determinism (double-run +
insertion-order), envelope/error-frame compliance, and scope hygiene.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import clean_min as C
from research_tool.nine_loop import extract_min as E

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN_C = FIXTURES / "golden_delta_c"
GOLDEN_E = FIXTURES / "golden_delta_e"


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def golden_lines(base):
    inp = (base / "input.jsonl").read_text(encoding="utf-8").splitlines()
    out = (base / "output.jsonl").read_text(encoding="utf-8").splitlines()
    return inp, out


def canon(env):
    return json.dumps(env, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


# --------------------------------------------------------------------------- #
# 1. ② clean golden
# --------------------------------------------------------------------------- #
class TestCleanGolden(unittest.TestCase):
    def test_sha_manifest_c(self):
        for entry in (GOLDEN_C / "SHA256SUMS").read_text().splitlines():
            s, n = entry.split()
            self.assertEqual(sha256_file(GOLDEN_C / n), s, n)

    def test_cleaned_byte_identical(self):
        inp, out = golden_lines(GOLDEN_C)
        for i, line in enumerate(inp):
            env = json.loads(line)
            got = C.canonical_output_bytes(C.run_clean(C.request_from_collect(env)))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_double_run(self):
        for line in golden_lines(GOLDEN_C)[0]:
            env = json.loads(line)
            a = C.canonical_output_bytes(C.run_clean(C.request_from_collect(env)))
            b = C.canonical_output_bytes(C.run_clean(C.request_from_collect(env)))
            self.assertEqual(a, b)

    def test_error_frames(self):
        env = json.loads(golden_lines(GOLDEN_C)[0][0])
        req = C.request_from_collect(env)
        req["v"] = 2
        got = C.run_clean(req)
        self.assertIsNone(got["result"])
        self.assertEqual(got["error"]["code"], C.E_VERSION)


# --------------------------------------------------------------------------- #
# 2. ③ extract golden
# --------------------------------------------------------------------------- #
class TestExtractGolden(unittest.TestCase):
    def test_sha_manifest_e(self):
        for entry in (GOLDEN_E / "SHA256SUMS").read_text().splitlines():
            s, n = entry.split()
            self.assertEqual(sha256_file(GOLDEN_E / n), s, n)

    def test_facts_byte_identical(self):
        inp, out = golden_lines(GOLDEN_E)
        for i, line in enumerate(inp):
            clean_env = json.loads(line)
            got = E.canonical_output_bytes(
                E.run_extract(E.request_from_clean(clean_env)))
            self.assertEqual(got.decode().rstrip("\n"), out[i], f"line {i}")

    def test_facts_have_evidence_spans(self):
        line = golden_lines(GOLDEN_E)[1][0]
        env = json.loads(line)
        for fact in env["result"]["facts"]:
            self.assertEqual(fact["extractor_version"], E.EXTRACTOR_VERSION)
            self.assertIn("locator", fact["span"])
            self.assertEqual(fact["span"]["start_offset"], 0)
            self.assertEqual(fact["span"]["end_offset"],
                             len(fact["span"]["locator"]))

    def test_insertion_order(self):
        line = golden_lines(GOLDEN_E)[0][0]
        clean_env = json.loads(line)
        shuffled = {k: clean_env[k] for k in reversed(list(clean_env.keys()))}
        got = E.canonical_output_bytes(E.run_extract(E.request_from_clean(shuffled)))
        self.assertEqual(got.decode().rstrip("\n"), golden_lines(GOLDEN_E)[1][0])


# --------------------------------------------------------------------------- #
# 3. Delta idempotency (content_sha256 skip)
# --------------------------------------------------------------------------- #
class TestDeltaIdempotent(unittest.TestCase):
    def test_clean_second_run_zero_new(self):
        line = golden_lines(GOLDEN_C)[0][0]
        env = json.loads(line)
        seeds = env["result"]["seeds"]
        seen = set()
        first = C.run_clean(C.request_from_collect(env), seen_keys=seen)
        second = C.run_clean(C.request_from_collect(env), seen_keys=seen)
        self.assertGreater(first["result"]["counts"]["new_facts"], 0)
        self.assertEqual(second["result"]["counts"]["new_facts"], 0)
        # second run skips every seed record (delta semantics)
        self.assertEqual(second["result"]["counts"]["skipped"],
                         len(seeds))

    def test_extract_second_run_zero_new(self):
        line = golden_lines(GOLDEN_E)[0][0]
        clean_env = json.loads(line)
        seen = set()
        first = E.run_extract(E.request_from_clean(clean_env), seen_keys=seen)
        second = E.run_extract(E.request_from_clean(clean_env), seen_keys=seen)
        self.assertGreater(first["result"]["counts"]["new_facts"], 0)
        self.assertEqual(second["result"]["counts"]["new_facts"], 0)


# --------------------------------------------------------------------------- #
# 4. Scope hygiene
# --------------------------------------------------------------------------- #
class TestScopeHygiene(unittest.TestCase):
    def test_no_network_or_llm_imports(self):
        nine_loop = pathlib.Path(__file__).resolve().parents[1] / "nine_loop"
        for mod in ("clean_min", "extract_min"):
            src = (nine_loop / f"{mod}.py").read_text(encoding="utf-8")
            for banned in ("socket", "urllib", "http.client", "requests",
                           "ssl", "asyncio", "subprocess", "openai",
                           "litellm", "anthropic"):
                self.assertNotIn(f"import {banned}", src, mod)
                self.assertNotIn(f"from {banned}", src, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
