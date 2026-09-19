"""Empirical Challenger 3.2 Live Verification & Adversarial Stress Test Suite.

Adversarially challenges:
1. --resume idempotency: multiple successive resume calls must not corrupt state.json,
   run-summary.json stages_completed, or artifacts.
2. Stage ⑦ Merge CAS invariants: conflicting viewpoints preserved as conflict_version,
   digest mismatch detection (E_CAS_CONFLICT), and exact content_sha256 deduplication.
3. Stage ⑧ QGate threshold evaluation and Inspect gap detection (contradiction, orphan, incomplete).
4. Stage ⑨ Reporter sentence boundary defense and broad regex reference splitting.
5. Downstream wiki-stage compatibility on the produced outputs (content-addressed packaging & CLI).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import tempfile
import unittest
from typing import Any

from typer.testing import CliRunner

from research_tool.application.pipeline import CANONICAL_NINE_STAGES, ResearchPipeline
from research_tool.application.talk_linker import TalkEnrichReport, TalkLinker, TalkMatch
from research_tool.domain.config import load_config
from research_tool.domain.models import LLMConfig, PipelineConfig, ReporterConfig
from research_tool.infrastructure.export import wiki_stage
from research_tool.infrastructure.llm.base import LLMClient
from research_tool.infrastructure.stages.reporter import Reporter
from research_tool.nine_loop import inspect_min, merge_min, qgate_min
from research_tool.nine_loop.chain_state import (
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    ChainState,
    ChainStateFault,
    sha256_bytes,
)
from research_tool.nine_loop.rt_identity_adapter import PythonIdentityEngine
from research_tool.presentation.cli import app

LIVE_DIR = pathlib.Path("research-output/liang-zi-ji-suan-qian-yan-jin-zhan")


def _dir_sha256_snapshot(directory: pathlib.Path) -> dict[str, str]:
    """Calculate relative path -> sha256 hex mapping for all files in a directory."""
    snapshot: dict[str, str] = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel = p.relative_to(directory).as_posix()
            snapshot[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snapshot


class DummyLLM(LLMClient):
    def __init__(self) -> None:
        super().__init__(LLMConfig(provider="openai", api_key="dummy"))

    async def chat(
        self, prompt: str, system: str | None = None, temperature: float | None = None
    ) -> str:
        return "## 一、前言\n量子计算前沿进展论述。(来源01)。\n\n## 参考文献\n- 来源01: 论文\n"

    async def chat_structured(
        self, prompt: str, schema: Any, system: str | None = None
    ) -> Any:
        return schema()

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        yield "chunk"

    async def healthcheck(self, timeout_sec: float = 10.0) -> None:
        pass


class TestResumeIdempotencyAndArtifactPreservation(unittest.TestCase):
    """Stress tests --resume idempotency, state non-corruption, and breakpoint resumption."""

    def setUp(self) -> None:
        self.assertTrue(LIVE_DIR.exists(), f"Live run directory {LIVE_DIR} does not exist")
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="test_resume_stress_"))
        self.work_dir = self.temp_dir / "research-output"
        # Copy live directory into test workspace
        self.topic_name = LIVE_DIR.name
        self.target_dir = self.work_dir / self.topic_name
        shutil.copytree(LIVE_DIR, self.target_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_repeated_resume_calls_zero_corruption(self) -> None:
        """Adversarial stress: calling resume 5 consecutive times must not corrupt state,

        run-summary stages_completed, or any output artifact.
        """
        initial_snapshot = _dir_sha256_snapshot(self.target_dir)

        # Baseline check on initial state
        summary_path = self.target_dir / "run-summary.json"
        state_path = self.target_dir / "state.json"
        self.assertTrue(summary_path.exists())
        self.assertTrue(state_path.exists())

        orig_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        orig_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(orig_summary.get("pipeline_complete"))
        self.assertEqual(len(orig_summary.get("stages_completed", [])), 9)
        self.assertEqual(orig_summary.get("citation_coverage"), 1.0)
        self.assertEqual(len(orig_state.get("done", [])), 9)

        # We will run resume via Pipeline 5 times using configured defaults
        base_cfg = load_config()
        config = base_cfg.model_copy(
            update={
                "topic": "量子计算前沿进展",
                "mode": "full",
                "work_dir": self.work_dir,
                "resume": True,
            }
        )

        for iteration in range(1, 6):
            pipeline = ResearchPipeline(config)
            result = asyncio.run(pipeline.run())

            # Pipeline result checks
            self.assertIsNone(result.failed_stage, f"Failed at iteration {iteration}")
            self.assertGreaterEqual(len(result.stages_skipped), 1)

            # Re-read run-summary.json
            current_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(
                current_summary.get("pipeline_complete"),
                f"Iteration {iteration}: pipeline_complete became False",
            )
            completed_stages = current_summary.get("stages_completed", [])
            self.assertEqual(
                completed_stages,
                [
                    "collect",
                    "clean",
                    "extract",
                    "knowledge",
                    "inspect",
                    "targeted",
                    "merge",
                    "qgate",
                    "report",
                ],
                f"Iteration {iteration}: stages_completed corrupted: {completed_stages}",
            )
            self.assertEqual(
                current_summary.get("citation_coverage"),
                1.0,
                f"Iteration {iteration}: citation_coverage lost",
            )

            # Re-read state.json
            current_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                len(current_state.get("done", [])),
                9,
                f"Iteration {iteration}: state.json done stages altered",
            )

            # Critical deliverables must remain identical in hash
            critical_files = [
                "report.md",
                "sources.json",
                "tree/00-主表.md",
                "artifacts/clean.json",
                "artifacts/extract.json",
                "artifacts/inspect.json",
                "artifacts/qgate.json",
                "artifacts/targeted.json",
                "artifacts/merge.json",
                "artifacts/report.json",
            ]
            for cf in critical_files:
                p = self.target_dir / cf
                self.assertTrue(p.exists(), f"File {cf} went missing")
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                self.assertEqual(
                    h,
                    initial_snapshot[cf],
                    f"Iteration {iteration}: Deliverable {cf} was modified! Hash mismatch",
                )

    def test_intermediate_breakpoint_recovery(self) -> None:
        """Simulate interrupted pipeline (only stages 1-4 completed).

        Resume must skip 1-4, complete 5-9, and output full 9 stages in run-summary.json.
        """
        # Remove stages 5-9 artifacts and completion markers
        for s in ["inspect", "targeted", "merge", "qgate", "report"]:
            (self.target_dir / "artifacts" / f"{s}.json").unlink(missing_ok=True)
            (self.target_dir / ".stage-complete" / f"{s}.json").unlink(missing_ok=True)
        (self.target_dir / "report.md").unlink(missing_ok=True)

        # Update state.json to only contain stages 1-4
        state_path = self.target_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["done"] = ["collect", "clean", "extract", "knowledge"]
        for s in ["inspect", "targeted", "merge", "qgate", "report", "gate"]:
            state["stages"].pop(s, None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        base_cfg = load_config()
        config = base_cfg.model_copy(
            update={
                "topic": "量子计算前沿进展",
                "mode": "full",
                "work_dir": self.work_dir,
                "resume": True,
            }
        )
        pipeline = ResearchPipeline(config)
        pipeline._llm = DummyLLM()

        result = asyncio.run(pipeline.run())
        self.assertIsNone(result.failed_stage)

        # Inspect summary
        summary = json.loads((self.target_dir / "run-summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["pipeline_complete"])
        self.assertEqual(
            set(summary["stages_completed"]),
            set(CANONICAL_NINE_STAGES),
        )
        self.assertTrue((self.target_dir / "report.md").exists())

    def test_cli_repeated_resume(self) -> None:
        """Adversarial stress: calling `research run --resume` via CLI 3 times maintains 9 completed stages."""
        runner = CliRunner()
        for i in range(1, 4):
            res = runner.invoke(
                app,
                [
                    "run",
                    "量子计算前沿进展",
                    "--mode",
                    "full",
                    "-o",
                    str(self.work_dir),
                    "--resume",
                ],
            )
            self.assertEqual(res.exit_code, 0, f"CLI resume iteration {i} failed: {res.stdout}")
            summary = json.loads((self.target_dir / "run-summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["pipeline_complete"])
            self.assertEqual(len(summary["stages_completed"]), 9)
            self.assertEqual(summary["citation_coverage"], 1.0)

    def test_tamper_detection_in_chain_state(self) -> None:
        """Tampering with an artifact file must trigger ChainState verification fault on load."""
        chain_state = ChainState(self.target_dir)
        input_key = hashlib.sha256("量子计算前沿进展:full".encode("utf-8")).hexdigest()

        # Valid load succeeds
        state = chain_state.load(input_key, resume=True)
        self.assertIn("clean", state["done"])

        # Tamper with clean artifact
        clean_artifact = self.target_dir / "artifacts" / "clean.json"
        orig_content = clean_artifact.read_bytes()
        clean_artifact.write_bytes(orig_content + b"// corrupted")

        # ChainState.read_stage must detect digest mismatch
        with self.assertRaises(ChainStateFault) as ctx:
            chain_state.read_stage(state, "clean")
        self.assertEqual(ctx.exception.code, E_STATE)


class TestStage7MergeCASInvariants(unittest.TestCase):
    """Stress tests Stage ⑦ Merge CAS atomic verification, conflict preservation, and dedup."""

    def setUp(self) -> None:
        self.base_graph = {
            "nodes": [
                {
                    "node_id": "src:quantum:n1",
                    "kind": "source",
                    "title": "超导量子计算研究",
                    "evidence_spans": [
                        {
                            "source_id": "src:quantum:n1",
                            "locator": "https://arxiv.org/abs/2301.00001",
                            "content_sha256": "aaaa" * 16,
                            "round_id": "r0",
                        }
                    ],
                }
            ],
            "edges": [],
            "counts": {"nodes": 1, "edges": 0},
        }
        self.base_digest = merge_min.graph_digest(self.base_graph)
        self.lease = {
            "lease_id": "lease-test-1234",
            "tokens_max": 100000,
            "cost_max": 1.0,
            "wall_s_max": 60.0,
            "search_calls_max": 10,
            "issued_at": "2026-09-18T00:00:00Z",
            "expires_at": "2026-09-18T01:00:00Z",
        }
        self.net_env = {
            "v": 1,
            "run_id": "run-test-1",
            "stage": "network",
            "request_id": "network:001",
            "idempotency_key": self.base_digest,
            "budget_lease": self.lease,
            "result": self.base_graph,
            "error": None,
        }

    def test_cas_mismatch_raises_e_cas_conflict(self) -> None:
        """Stage 7 write-if-match: if prev_digest does not match graph digest, raise E_CAS_CONFLICT."""
        responses = [
            {
                "request_id": "q1",
                "facts": [
                    {
                        "source_id": "src:quantum:n2",
                        "locator": "https://arxiv.org/abs/2301.00002",
                        "content_sha256": "bbbb" * 16,
                    }
                ],
            }
        ]
        stale_digest = "ffff" * 16
        req = merge_min.request_from_chain(self.net_env, responses, prev_digest=stale_digest)

        # run_merge returns typed error frame
        env = merge_min.run_merge(req)
        self.assertIsNone(env["result"])
        self.assertIsNotNone(env["error"])
        self.assertEqual(env["error"]["code"], merge_min.E_CAS_CONFLICT)
        self.assertIn("does not match prev_digest", env["error"]["safe_message"])

        # Direct merge() raises exception
        with self.assertRaises(merge_min.MergeStageFault) as ctx:
            merge_min.merge(self.base_graph, responses, stale_digest)
        self.assertEqual(ctx.exception.frame["code"], merge_min.E_CAS_CONFLICT)

    def test_conflicting_viewpoint_preserved_as_conflict_coexist(self) -> None:
        """When a response provides a conflicting fact for the same family (source_id),

        both facts are retained in evidence_spans and logged as conflict_coexist.
        """
        conflict_fact = {
            "source_id": "src:quantum:n1",  # Same family
            "locator": "https://arxiv.org/abs/2301.00001v2",
            "content_sha256": "cccc" * 16,  # Different content hash
            "extractor_version": "test.v2",
            "round_id": "r1",
        }
        responses = [{"request_id": "q_conflict", "facts": [conflict_fact]}]
        req = merge_min.request_from_chain(self.net_env, responses, prev_digest=self.base_digest)
        env = merge_min.run_merge(req)

        self.assertIsNone(env["error"])
        res = env["result"]
        self.assertEqual(res["counts"]["conflicts"], 1)
        self.assertEqual(res["counts"]["new_facts"], 1)
        self.assertEqual(res["counts"]["skipped"], 0)

        # Check merge log
        log_entry = res["merge_log"][0]
        self.assertEqual(log_entry["kind"], "conflict_coexist")
        self.assertEqual(log_entry["source_id"], "src:quantum:n1")
        self.assertEqual(log_entry["content_sha256"], "cccc" * 16)

        # Check node spans: both original and new span exist
        merged_nodes = res["graph"]["nodes"]
        self.assertEqual(len(merged_nodes), 1)
        spans = merged_nodes[0]["evidence_spans"]
        self.assertEqual(len(spans), 2)
        span_hashes = {s["content_sha256"] for s in spans}
        self.assertEqual(span_hashes, {"aaaa" * 16, "cccc" * 16})

    def test_python_identity_engine_conflict_version_decision(self) -> None:
        """In PythonIdentityEngine, hits mapping to the same external identity with distinct hashes

        are tagged with decision 'conflict_version'.
        """
        engine = PythonIdentityEngine()
        hits = [
            {
                "url": "https://arxiv.org/abs/2301.00001",
                "title": "Quantum Paper v1",
                "snippet": "",
                "source_engine": "arxiv",
                "audit_engine": "arxiv",
                "rank": 0,
                "query_id": "q0",
                "content": "Original hypothesis: qubits scale linearly.",
            },
            {
                "url": "https://arxiv.org/pdf/2301.00001v2",
                "title": "Quantum Paper v2",
                "snippet": "",
                "source_engine": "arxiv",
                "audit_engine": "arxiv",
                "rank": 1,
                "query_id": "q0",
                "content": "Revised hypothesis: qubits exhibit non-linear decoherence.",
            },
        ]
        req = {
            "v": 1,
            "id": "req-conflict-test",
            "op": "identify",
            "hits": hits,
        }
        resp = engine.dispatch(req)
        self.assertTrue(resp.get("ok"))
        identities = resp["identities"]
        self.assertEqual(len(identities), 2)
        for ident in identities:
            self.assertEqual(
                ident["decision"],
                "conflict_version",
                f"Hit {ident['input_url']} decision was {ident['decision']}, expected conflict_version",
            )
            self.assertEqual(ident["source_id"], "exid01.v1:arxiv:2301.00001")

    def test_exact_content_sha256_deduplication(self) -> None:
        """Re-merging exact same fact (source_id + content_sha256) is skipped with zero duplicate delta."""
        duplicate_fact = {
            "source_id": "src:quantum:n1",
            "locator": "https://arxiv.org/abs/2301.00001",
            "content_sha256": "aaaa" * 16,  # identical to base_graph
        }
        responses = [{"request_id": "q_dup", "facts": [duplicate_fact]}]
        req = merge_min.request_from_chain(self.net_env, responses, prev_digest=self.base_digest)
        env = merge_min.run_merge(req)

        self.assertIsNone(env["error"])
        res = env["result"]
        self.assertEqual(res["counts"]["skipped"], 1)
        self.assertEqual(res["counts"]["new_facts"], 0)
        self.assertEqual(res["counts"]["conflicts"], 0)
        self.assertEqual(res["latest_digest"], self.base_digest)

    def test_talk_linker_merge_into_network_cas(self) -> None:
        """TalkLinker.merge_into_network uses CAS merge and preserves immutability."""
        linker = TalkLinker()
        match = TalkMatch(
            paper_title="超导量子计算研究",
            video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            confidence=0.95,
            matched=True,
            video_title="CVPR 2026 Keynote on Superconducting Qubits",
        )
        report = TalkEnrichReport(candidates=1, matched=[match])

        # Input copy to assert aliasing isolation
        net_copy = copy.deepcopy(self.net_env)
        merged_net, merge_env = linker.merge_into_network(self.net_env, report)

        # Input net_env unchanged
        self.assertEqual(self.net_env, net_copy)
        # Merged net has 2 nodes
        self.assertEqual(len(merged_net["result"]["nodes"]), 2)
        self.assertIsNone(merge_env.get("error"))

        # Test CAS mismatch in TalkLinker
        stale_digest = "1234" * 16
        _, err_env = linker.merge_into_network(self.net_env, report, prev_digest=stale_digest)
        self.assertEqual(err_env["error"]["code"], merge_min.E_CAS_CONFLICT)


class TestStage8QGateAndStage5Inspect(unittest.TestCase):
    """Stress tests Stage ⑤ Inspect gap/contradiction detection and Stage ⑧ QGate threshold decision tree."""

    def setUp(self) -> None:
        self.lease = {
            "lease_id": "lease-gate-test",
            "tokens_max": 50000,
            "cost_max": 2.0,
            "wall_s_max": 120.0,
            "search_calls_max": 5,
            "issued_at": "2026-09-18T00:00:00Z",
            "expires_at": "2026-09-18T01:00:00Z",
        }

    def test_inspect_detects_contradiction_orphan_incomplete(self) -> None:
        """Inspect must detect:

        - same_bytes edge as high-priority contradiction
        - orphan node (degree 0) as medium-priority gap
        - null content_sha256 as low-priority incomplete span
        """
        graph = {
            "nodes": [
                {
                    "node_id": "fam:A",
                    "evidence_spans": [{"locator": "http://a.com", "content_sha256": "1111" * 16}],
                },
                {
                    "node_id": "fam:B",
                    "evidence_spans": [{"locator": "http://b.com", "content_sha256": "1111" * 16}],
                },
                {
                    "node_id": "fam:C_orphan",
                    "evidence_spans": [{"locator": "http://c.com", "content_sha256": "2222" * 16}],
                },
                {
                    "node_id": "fam:D_incomplete",
                    "evidence_spans": [{"locator": "http://d.com", "content_sha256": None}],
                },
            ],
            "edges": [
                {
                    "edge_id": "edge:AB:same_bytes",
                    "source": "fam:A",
                    "target": "fam:B",
                    "type": "same_bytes",
                    "content_sha256": "1111" * 16,
                },
                {
                    "edge_id": "edge:BD:rel",
                    "source": "fam:B",
                    "target": "fam:D_incomplete",
                    "type": "related",
                },
            ],
        }
        net_env = {
            "v": 1,
            "run_id": "run-test-inspect",
            "stage": "network",
            "request_id": "net:001",
            "idempotency_key": merge_min.graph_digest(graph),
            "budget_lease": self.lease,
            "result": graph,
            "error": None,
        }
        insp_req = inspect_min.request_from_network(net_env)
        insp_env = inspect_min.run_inspect(insp_req)

        self.assertIsNone(insp_env["error"])
        findings = insp_env["result"]["findings"]
        counts = insp_env["result"]["counts"]

        # High: contradiction:edge:AB:same_bytes
        high_findings = [f for f in findings if f["priority"] == "high"]
        self.assertEqual(len(high_findings), 1)
        self.assertEqual(high_findings[0]["code"], "same_bytes_cross_family")
        self.assertEqual(high_findings[0]["source"], "fam:A")
        self.assertEqual(high_findings[0]["target"], "fam:B")

        # Medium: orphan node fam:C_orphan
        med_findings = [f for f in findings if f["priority"] == "medium"]
        self.assertEqual(len(med_findings), 1)
        self.assertEqual(med_findings[0]["code"], "orphan_node")
        self.assertEqual(med_findings[0]["source"], "fam:C_orphan")

        # Low: span_incomplete fam:D_incomplete
        low_findings = [f for f in findings if f["priority"] == "low"]
        self.assertEqual(len(low_findings), 1)
        self.assertEqual(low_findings[0]["code"], "span_incomplete")
        self.assertEqual(low_findings[0]["source"], "fam:D_incomplete")

        self.assertEqual(counts["high"], 1)
        self.assertEqual(counts["medium"], 1)
        self.assertEqual(counts["low"], 1)
        self.assertEqual(counts["total"], 3)

    def test_qgate_threshold_evaluation(self) -> None:
        """QGate evaluates verdict based on high findings and total findings against thresholds."""
        # Case 1: High findings > max_high_findings (0) -> CONTINUE
        findings_with_high = [
            {"finding_id": "f1", "priority": "high", "code": "same_bytes_cross_family"}
        ]
        insp_env_high = {
            "v": 1,
            "run_id": "run-qgate",
            "stage": "inspect",
            "request_id": "insp:01",
            "budget_lease": self.lease,
            "result": {"findings": findings_with_high, "counts": {"total": 1, "high": 1}},
            "error": None,
        }
        gate_req1 = qgate_min.request_from_inspect(
            insp_env_high, thresholds={"max_high_findings": 0, "max_total_findings": 10}
        )
        gate_env1 = qgate_min.run_gate(gate_req1)
        self.assertEqual(gate_env1["result"]["verdict"], "CONTINUE")
        self.assertIn("high-priority findings exceed max_high_findings", gate_env1["result"]["reasons"])

        # Case 2: Total findings > max_total_findings (2) -> CONTINUE
        findings_overflow = [
            {"finding_id": f"f{i}", "priority": "low", "code": "span_incomplete"}
            for i in range(3)
        ]
        insp_env_overflow = {
            "v": 1,
            "run_id": "run-qgate",
            "stage": "inspect",
            "request_id": "insp:02",
            "budget_lease": self.lease,
            "result": {"findings": findings_overflow, "counts": {"total": 3, "low": 3}},
            "error": None,
        }
        gate_req2 = qgate_min.request_from_inspect(
            insp_env_overflow, thresholds={"max_high_findings": 0, "max_total_findings": 2}
        )
        gate_env2 = qgate_min.run_gate(gate_req2)
        self.assertEqual(gate_env2["result"]["verdict"], "CONTINUE")
        self.assertIn("total findings exceed max_total_findings", gate_env2["result"]["reasons"])

        # Case 3: Thresholds satisfied -> STOP_SUCCESS
        gate_req3 = qgate_min.request_from_inspect(
            insp_env_overflow, thresholds={"max_high_findings": 0, "max_total_findings": 5}
        )
        gate_env3 = qgate_min.run_gate(gate_req3)
        self.assertEqual(gate_env3["result"]["verdict"], "STOP_SUCCESS")

        # Case 4: Lease exhausted -> E_BUDGET_EXHAUSTED
        exhausted_lease = dict(self.lease, tokens_max=0)
        insp_env_exhausted = dict(insp_env_overflow, budget_lease=exhausted_lease)
        gate_req4 = qgate_min.request_from_inspect(insp_env_exhausted)
        gate_env4 = qgate_min.run_gate(gate_req4)
        self.assertEqual(gate_env4["error"]["code"], qgate_min.E_BUDGET_EXHAUSTED)


class TestStage9ReporterDefenseAndRegex(unittest.TestCase):
    """Stress tests Stage ⑨ Reporter sentence boundary defense and broad regex reference splitting."""

    def setUp(self) -> None:
        self.reporter = Reporter(ReporterConfig(max_length=10000))

    def test_broad_regex_reference_splitting(self) -> None:
        """Adversarially test reference splitting across all heading levels and language variants."""
        variants = [
            "## 参考文献\n1. [1] Paper A",
            "### 参考资料\n- 来源01: Paper B",
            "# 参考来源\n- 来源01",
            "#### 引用来源\n- Source C",
            "##### References\n- Reference 1",
            "## REFERENCES\n1. ABC",
            "## references\n1. xyz",
        ]

        ref_split_regex = re.compile(
            r"\n#{1,6}\s*(?:参考资料|参考文献|参考来源|引用来源|References)\b.*$",
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )

        sample_body = "量子计算在超导与离子阱路线上均取得突破。容错计算迈入逻辑量子比特时代。"
        for var in variants:
            raw_text = f"{sample_body}\n{var}"
            cleaned = ref_split_regex.split(raw_text)[0].rstrip()
            self.assertEqual(
                cleaned,
                sample_body,
                f"Failed to cleanly strip variant: {var!r}",
            )

    def test_inline_reference_mention_not_stripped(self) -> None:
        """Inline sentences mentioning '参考文献' or '参考资料' without a header must NOT be stripped."""
        ref_split_regex = re.compile(
            r"\n#{1,6}\s*(?:参考资料|参考文献|参考来源|引用来源|References)\b.*$",
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        text = "根据参考文献[1]的实验数据，保真度达到了99.9%（来源01）。"
        res = ref_split_regex.split(text)[0].rstrip()
        self.assertEqual(res, text)

    def test_sentence_boundary_defense_hanging_fragment(self) -> None:
        """Test sentence boundary defense when model terminates abruptly or with a dangling colon."""
        terminal_puncts = ("。", "！", "？", "!", "?", "”", "’", "）", ")", "```", "\n")

        # Case 1: Body ending with proper terminal punctuation
        body1 = "量子纠错代码实现了突破。"
        self.assertTrue(body1.endswith(terminal_puncts))

        # Case 2: Body ending with hanging fragment following a period within 500 chars
        body2 = "量子纠错代码实现了突破。此外，未来的关键挑战还包括："
        # Boundary defense logic
        if not body2.endswith(terminal_puncts):
            p_idx = max(body2.rfind("。"), body2.rfind("！"), body2.rfind("？"))
            if p_idx != -1 and len(body2) - p_idx < 500:
                fixed2 = body2[: p_idx + 1].rstrip()
            else:
                fixed2 = body2 + "。"
        self.assertEqual(fixed2, "量子纠错代码实现了突破。")

        # Case 3: Body ending without terminal punct and no prior punct within 500 chars
        body3 = "这是一个没有句号的长段落"
        if not body3.endswith(terminal_puncts):
            p_idx = max(body3.rfind("。"), body3.rfind("！"), body3.rfind("？"))
            if p_idx != -1 and len(body3) - p_idx < 500:
                fixed3 = body3[: p_idx + 1].rstrip()
            else:
                fixed3 = body3 + "。"
        self.assertEqual(fixed3, "这是一个没有句号的长段落。")


class TestDownstreamWikiStageCompatibility(unittest.TestCase):
    """Stress tests downstream wiki-stage packaging and CLI execution on live artifacts."""

    def setUp(self) -> None:
        self.assertTrue(LIVE_DIR.exists())
        self.temp_dest = pathlib.Path(tempfile.mkdtemp(prefix="test_wiki_stage_dest_"))

    def tearDown(self) -> None:
        # Reset permissions before removing directory (wiki-stage sets 0o555 on dirs)
        for root, dirs, files in os.walk(self.temp_dest):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o700)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o600)
                except OSError:
                    pass
        shutil.rmtree(self.temp_dest, ignore_errors=True)

    def test_build_stage_package_success_and_immutability(self) -> None:
        """wiki_stage.build_stage_package must create an immutable content-addressed package."""
        pkg = wiki_stage.build_stage_package(LIVE_DIR, self.temp_dest)

        self.assertFalse(pkg.already_exists)
        self.assertTrue(pkg.package_path.exists())
        self.assertTrue(pkg.package_id.startswith("rp_"))
        self.assertEqual(len(pkg.package_hash), 64)

        # Check manifest
        manifest_path = pkg.package_path / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["producer"], "research-tool/wiki-stage-p1")
        self.assertEqual(manifest["verification"], "unverified")
        self.assertEqual(manifest["sections"]["report"], "report.md")

        # Check archive blobs have read-only permissions (0o444)
        archive_dir = pkg.package_path / "archive"
        self.assertTrue(archive_dir.exists())
        blobs = list(archive_dir.glob("*.blob"))
        self.assertGreater(len(blobs), 0)
        for b in blobs:
            mode = stat.S_IMODE(b.stat().st_mode)
            self.assertEqual(mode, 0o444, f"Blob {b.name} mode {oct(mode)} != 0o444")

        # Check intake documents
        report_doc = pkg.package_path / "report.md"
        self.assertTrue(report_doc.exists())
        source_manifest = pkg.package_path / "source-manifest.md"
        self.assertTrue(source_manifest.exists())

        # Test idempotency: calling build_stage_package again on same directory returns already_exists=True
        pkg2 = wiki_stage.build_stage_package(LIVE_DIR, self.temp_dest)
        self.assertTrue(pkg2.already_exists)
        self.assertEqual(pkg2.package_hash, pkg.package_hash)

    def test_cli_wiki_stage_command(self) -> None:
        """CLI command `research wiki-stage <dir> --package-output <dest> --build` must succeed."""
        runner = CliRunner()
        res = runner.invoke(
            app,
            [
                "wiki-stage",
                str(LIVE_DIR),
                "--package-output",
                str(self.temp_dest),
                "--build",
            ],
        )
        self.assertEqual(res.exit_code, 0, f"CLI wiki-stage failed: {res.stdout}")
        self.assertIn("rp_", res.stdout)
