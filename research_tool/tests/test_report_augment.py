"""Offline mock-transport tests for report_augment (C2) — no real model calls.

Covers: prompt construction, output parsing (JSON, code fence stripping,
INSUFFICIENT), validation (weight sum, source_ids, schema), fallback on
model failure, merge path.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

from research_tool.nine_loop import report_augment as RA
from research_tool.nine_loop import model_adapter as MA

MANIFEST_IDS = {"ds.v1:test:1", "ds.v1:test:2", "ds.v1:test:3"}
TASK = {"case_id": "test", "question": "What is 1+1?", "required_dimensions": ["math"],
        "weighted_gold_claims": [{"claim": "2", "weight": 1.0, "source_ids": ["ds.v1:test:1"]}]}
REPORT = {"verdict": "STOP_SUCCESS", "report": {"claims": [{"text": "tmpl", "citations": []}]},
          "counts": {"claims": 1, "citations": 0, "dropped_claims": 0}}


def ok_transport(content: str, usage: dict | None = None):
    def t(url, body, key):
        return 200, json.dumps(
            {"id": "x", "model": "deepseek-v4-flash",
             "choices": [{"message": {"content": content}}],
             "usage": usage or {"total_tokens": 10}}).encode()
    return t


class TestPrompt(unittest.TestCase):
    def test_build_prompt_includes_fields(self):
        p = RA.build_prompt(TASK, "evidence text", "STOP_SUCCESS")
        self.assertIn("What is 1+1?", p)
        self.assertIn("math", p)
        self.assertIn("STOP_SUCCESS", p)
        self.assertIn("evidence text", p)

    def test_prompt_template_matches_design(self):
        p = RA.build_prompt(TASK, "e", "SUCCESS")
        self.assertIn("weighted claims", p)
        self.assertIn("Sum of weights = 1.0", p)
        self.assertIn("source_ids", p)
        # C3: exact-wording instruction present
        self.assertIn("EXACT factual wording", p)
        self.assertIn("avoid paraphrasing", p)


class TestParsing(unittest.TestCase):
    def test_parse_valid_json(self):
        claims = [{"claim": "c1", "weight": 0.5, "source_ids": ["ds.v1:test:1"]},
                  {"claim": "c2", "weight": 0.5, "source_ids": ["ds.v1:test:2"]}]
        text = json.dumps(claims)
        parsed = RA.parse_claims(text, MANIFEST_IDS)
        self.assertEqual(len(parsed), 2)

    def test_weight_sum_validation(self):
        claims = [{"claim": "c1", "weight": 0.3, "source_ids": ["ds.v1:test:1"]}]
        text = json.dumps(claims)
        with self.assertRaises(RA.AugmentFault) as ctx:
            RA.parse_claims(text, MANIFEST_IDS)
        self.assertIn("weight sum", ctx.exception.safe_message)

    def test_source_id_not_in_manifest(self):
        claims = [{"claim": "c1", "weight": 1.0, "source_ids": ["ds.v1:unknown"]}]
        text = json.dumps(claims)
        with self.assertRaises(RA.AugmentFault) as ctx:
            RA.parse_claims(text, MANIFEST_IDS)
        self.assertEqual(ctx.exception.code, "augment.E_SOURCE")

    def test_code_fence_stripping(self):
        claims = [{"claim": "c1", "weight": 1.0, "source_ids": ["ds.v1:test:1"]}]
        text = "```json\n" + json.dumps(claims) + "\n```"
        parsed = RA.parse_claims(text, MANIFEST_IDS)
        self.assertEqual(len(parsed), 1)

    def test_insufficient_marker(self):
        text = "INSUFFICIENT: evidence not enough"
        with self.assertRaises(RA.AugmentFault) as ctx:
            RA.parse_claims(text, MANIFEST_IDS)
        self.assertEqual(ctx.exception.code, "augment.E_INSUFFICIENT")

    def test_empty_output(self):
        with self.assertRaises(RA.AugmentFault) as ctx:
            RA.parse_claims("", MANIFEST_IDS)
        self.assertEqual(ctx.exception.code, "augment.E_PARSE")

    def test_structure_missing_fields(self):
        text = json.dumps([{"claim": "c1", "weight": 1.0}])  # missing source_ids
        with self.assertRaises(RA.AugmentFault):
            RA.parse_claims(text, MANIFEST_IDS)


class TestAugmentFlow(unittest.TestCase):
    def setUp(self):
        import os
        self._old_key = os.environ.get("DEEPSEEK_V4_API_KEY")
        os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-test"

    def tearDown(self):
        import os
        if self._old_key is None:
            os.environ.pop("DEEPSEEK_V4_API_KEY", None)
        else:
            os.environ["DEEPSEEK_V4_API_KEY"] = self._old_key

    def test_model_failure_returns_degraded(self):
        def t(url, body, key):
            return 500, b"boom"
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "degraded")
        self.assertEqual(result["augment"]["reason"], "model_fault")

    def test_validation_failure_returns_degraded(self):
        claims = [{"claim": "c1", "weight": 0.3,
                   "source_ids": ["ds.v1:test:1"]}]
        def t(url, body, key):
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {"content": json.dumps(claims)}}],
                 "usage": {"total_tokens": 5}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "degraded")
        self.assertEqual(result["augment"]["reason"], "validation_failed")

    def test_success_path_returns_augmented(self):
        claims = [{"claim": "c1", "weight": 1.0,
                   "source_ids": ["ds.v1:test:1"]}]
        def t(url, body, key):
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {"content": json.dumps(claims)}}],
                 "usage": {"total_tokens": 10}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "augmented")
        self.assertEqual(len(result["augment"]["model_claims"]), 1)
        self.assertEqual(result["augment"]["model_claims"][0]["claim"], "c1")


class TestRetryC6(unittest.TestCase):
    """C6: E_PARSE retry-once with simplified format constraint."""

    def setUp(self):
        import os
        self._old_key = os.environ.get("DEEPSEEK_V4_API_KEY")
        os.environ["DEEPSEEK_V4_API_KEY"] = "sk-offline-test"

    def tearDown(self):
        import os
        if self._old_key is None:
            os.environ.pop("DEEPSEEK_V4_API_KEY", None)
        else:
            os.environ["DEEPSEEK_V4_API_KEY"] = self._old_key

    def test_empty_output_retried_success(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            if len(calls) == 1:
                return 200, json.dumps(
                    {"id": "x", "model": "deepseek-v4-flash",
                     "choices": [{"message": {"content": ""}}],
                     "usage": {"total_tokens": 5}}).encode()
            claims = [{"claim": "c1", "weight": 1.0,
                       "source_ids": ["ds.v1:test:1"]}]
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {"content": json.dumps(claims)}}],
                 "usage": {"total_tokens": 10}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "augmented")
        self.assertTrue(result["augment"].get("retried"))
        self.assertEqual(len(result["augment"]["model_claims"]), 1)
        self.assertEqual(len(calls), 2)

    def test_insufficient_never_retried(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {
                     "content": "INSUFFICIENT: evidence not enough"}}],
                 "usage": {}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "degraded")
        self.assertEqual(result["augment"]["code"], "augment.E_INSUFFICIENT")
        self.assertEqual(len(calls), 1)

    def test_retry_still_empty_stays_degraded(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {"content": ""}}],
                 "usage": {}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "degraded")
        self.assertEqual(len(calls), 2)

    def test_retry_output_still_validated(self):
        calls = []

        def t(url, body, key):
            calls.append(1)
            if len(calls) == 1:
                return 200, json.dumps(
                    {"id": "x", "model": "deepseek-v4-flash",
                     "choices": [{"message": {"content": ""}}],
                     "usage": {}}).encode()
            claims = [{"claim": "c1", "weight": 0.3,
                       "source_ids": ["ds.v1:test:1"]}]
            return 200, json.dumps(
                {"id": "x", "model": "deepseek-v4-flash",
                 "choices": [{"message": {"content": json.dumps(claims)}}],
                 "usage": {}}).encode()
        result = RA.augment_report(REPORT, TASK, MANIFEST_IDS, "evidence",
                                   transport=t)
        self.assertEqual(result["augment"]["status"], "degraded")


class TestScopeHygiene(unittest.TestCase):
    def test_no_third_party_imports(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/report_augment.py").read_text(
            encoding="utf-8")
        for banned in ("openai", "requests", "httpx", "anthropic"):
            self.assertNotIn(f"import {banned}", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
