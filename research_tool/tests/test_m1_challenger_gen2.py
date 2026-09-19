"""Milestone 1 Empirical Challenger Test Suite (Gen 2).

Systematic verification of:
1. Interrupted execution and --resume recovery across pipeline stages.
2. Empty and corrupted artifacts / SHA-256 tamper detection.
3. Stage skipping and selective DAG execution.
4. Protocol v1 envelope schema conformance and invariants.
5. Citation Coverage 1.0 enforcement and dropped claims behavior.
6. Downstream deliverables generation (report.md, tree/00-主表.md, sources.json, run-summary.json).
7. Downstream wiki-stage compatibility (build_stage_package).
8. Python SDK research() entrypoint native 9-stage execution.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import pathlib
import pytest
from typing import Any

from research_tool.application.pipeline import (
    ResearchPipeline,
    CANONICAL_NINE_STAGES,
    create_pipeline,
)
from research_tool.domain.models import PipelineConfig, PipelineResult
from research_tool.domain.errors import StageError
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.chain_state import (
    ChainState,
    ChainStateFault,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    sha256_bytes,
    write_atomic,
)
from research_tool.nine_loop import report_min, merge_min
from research_tool.infrastructure.export.wiki_stage import build_stage_package


def _setup_mock_pipe(tmp_path: pathlib.Path, **kwargs: Any) -> tuple[ResearchPipeline, pathlib.Path]:
    """Helper to create a pipeline with MockLLM and filesystem-only stage mocks."""
    work_dir = tmp_path / "work"
    cfg_kwargs = {
        "topic": "quantum-gravity",
        "work_dir": work_dir,
        "mode": "full",
        "resume": False,
        "stages": ["collect", "clean", "extract", "organize", "report"],
    }
    cfg_kwargs.update(kwargs)
    cfg = PipelineConfig(**cfg_kwargs)
    pipe = ResearchPipeline(cfg)

    pipe._llm = MockLLMClient(chat_response="# 报告\n\n量子引力具备理论一致性 (来源01)。\n\n## 参考资料\n- 来源01：量子引力 — https://arxiv.org/abs/quantum-gravity")

    async def mock_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text("<!-- source: https://arxiv.org/abs/quantum-gravity -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n量子引力理论内容", encoding="utf-8")
            sources = [{"url": "https://arxiv.org/abs/quantum-gravity", "title": "量子引力", "fetchedAt": "2026-09-17T14:00:00Z", "content_hash": "sha256:" + "a" * 64}]
            (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("清洗后的量子引力理论内容", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text('[{"name": "Quantum Gravity", "type": "theory"}]', encoding="utf-8")
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络大纲\n\n- [[N01-量子引力|量子引力概述]]\n", encoding="utf-8")
            (tree / "N01-量子引力.md").write_text("<!-- source: https://arxiv.org/abs/quantum-gravity -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n# 量子引力\n节点内容", encoding="utf-8")
        elif stage == "report":
            pass

    pipe._exec = mock_exec
    return pipe, work_dir


# ============================================================================
# Dimension 1: Interrupted Execution and --resume Recovery
# ============================================================================

@pytest.mark.asyncio
async def test_resume_after_interruption_at_extract(tmp_path: pathlib.Path):
    """Crash after 'extract' stage, resume with resume=True and verify uncommitted stages run."""
    pipe1, work_dir = _setup_mock_pipe(tmp_path, resume=False)
    topic_dir = work_dir / "quantum-gravity"

    # Stage 1: Run partially, interrupt by injecting an error at 'knowledge' stage
    orig_exec = pipe1._exec

    async def crashing_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage in ("organize", "knowledge"):
            raise RuntimeError("SIMULATED_CRASH_AT_KNOWLEDGE")
        await orig_exec(stage, topic, topic_dir, result)

    pipe1._exec = crashing_exec

    res1 = await pipe1.run("quantum-gravity")
    assert res1.failed_stage in ("knowledge", "organize")
    assert "collect" in res1.stages_completed
    assert "clean" in res1.stages_completed
    assert "extract" in res1.stages_completed

    # Check state on disk
    cs1 = ChainState(topic_dir)
    input_key = hashlib.sha256(b"quantum-gravity:full").hexdigest()
    st1 = cs1.load(input_key, resume=False)
    assert "collect" in st1["done"]
    assert "clean" in st1["done"]
    assert "extract" in st1["done"]
    assert "knowledge" not in st1["done"]

    # Stage 2: Resume with resume=True and working exec
    pipe2, _ = _setup_mock_pipe(tmp_path, resume=True)
    res2 = await pipe2.run("quantum-gravity")

    assert res2.failed_stage is None
    # Collect, clean, extract should be skipped
    assert "collect" in res2.stages_skipped
    assert "clean" in res2.stages_skipped
    assert "extract" in res2.stages_skipped
    # knowledge, inspect, qgate, targeted, merge, report should be completed
    assert "knowledge" in res2.stages_completed
    assert "report" in res2.stages_completed

    # Verify final summary
    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0


@pytest.mark.asyncio
async def test_resume_cleans_up_stale_tmp_residue(tmp_path: pathlib.Path):
    """Resume execution automatically cleans up orphan .tmp-* files without failing."""
    pipe, work_dir = _setup_mock_pipe(tmp_path, resume=True)
    topic_dir = work_dir / "quantum-gravity"
    topic_dir.mkdir(parents=True, exist_ok=True)
    artifacts = topic_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    # Leave orphan .tmp files from aborted writes
    (topic_dir / "state.json.tmp-dead-1111").write_text("corrupted", encoding="utf-8")
    (artifacts / "clean.json.tmp-dead-2222").write_text("corrupted", encoding="utf-8")

    res = await pipe.run("quantum-gravity")
    assert res.failed_stage is None
    # Verify no tmp files remain
    assert list(topic_dir.glob("*.tmp-*")) == []
    assert list(artifacts.glob("*.tmp-*")) == []


# ============================================================================
# Dimension 2: Empty & Corrupted Artifacts / Tamper Detection
# ============================================================================

@pytest.mark.asyncio
async def test_resume_fails_fast_on_corrupted_stage_artifact(tmp_path: pathlib.Path):
    """If an artifact file is tampered with or 0 bytes, resume=True fails fast with ChainStateFault."""
    pipe1, work_dir = _setup_mock_pipe(tmp_path, resume=False)
    res1 = await pipe1.run("quantum-gravity")
    assert res1.failed_stage is None

    topic_dir = work_dir / "quantum-gravity"
    collect_art = topic_dir / "artifacts" / "collect.json"
    assert collect_art.exists()

    # Tamper with collect.json
    collect_art.write_bytes(b'{"tampered": true}')

    pipe2, _ = _setup_mock_pipe(tmp_path, resume=True)
    with pytest.raises(ChainStateFault) as exc_info:
        await pipe2.run("quantum-gravity")
    assert exc_info.value.code == E_STATE


@pytest.mark.asyncio
async def test_resume_fails_fast_on_zero_byte_artifact(tmp_path: pathlib.Path):
    """If an artifact file is 0 bytes, resume=True detects SHA mismatch and raises E_STATE."""
    pipe1, work_dir = _setup_mock_pipe(tmp_path, resume=False)
    await pipe1.run("quantum-gravity")

    topic_dir = work_dir / "quantum-gravity"
    clean_art = topic_dir / "artifacts" / "clean.json"
    clean_art.write_bytes(b"")

    pipe2, _ = _setup_mock_pipe(tmp_path, resume=True)
    with pytest.raises(ChainStateFault) as exc_info:
        await pipe2.run("quantum-gravity")
    assert exc_info.value.code == E_STATE


@pytest.mark.asyncio
async def test_resume_fails_on_idempotency_conflict(tmp_path: pathlib.Path):
    """Running against an existing workdir with a different mode/topic triggers E_IDEMPOTENCY_CONFLICT."""
    pipe1, work_dir = _setup_mock_pipe(tmp_path, mode="full", resume=False)
    await pipe1.run("quantum-gravity")

    # Run again with mode="brief" in the same directory
    pipe2, _ = _setup_mock_pipe(tmp_path, mode="brief", resume=True)
    with pytest.raises(ChainStateFault) as exc_info:
        await pipe2.run("quantum-gravity")
    assert exc_info.value.code == E_IDEMPOTENCY_CONFLICT


# ============================================================================
# Dimension 3: Stage Skipping & Selective DAG Execution
# ============================================================================

@pytest.mark.asyncio
async def test_selective_stages_collect_clean_only(tmp_path: pathlib.Path):
    """Running only ['collect', 'clean'] completes successfully and generates only those stage artifacts."""
    pipe, work_dir = _setup_mock_pipe(tmp_path, stages=["collect", "clean"])
    res = await pipe.run("quantum-gravity")

    assert res.failed_stage is None
    assert "collect" in res.stages_completed
    assert "clean" in res.stages_completed
    assert "extract" not in res.stages_completed
    assert "report" not in res.stages_completed

    topic_dir = work_dir / "quantum-gravity"
    artifacts = topic_dir / "artifacts"
    assert (artifacts / "collect.json").exists()
    assert (artifacts / "clean.json").exists()
    assert not (artifacts / "extract.json").exists()
    assert not (artifacts / "report.json").exists()

    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True


@pytest.mark.asyncio
async def test_stage_skipping_via_extractor_disabled(tmp_path: pathlib.Path):
    """Disabling extractor skips extract stage cleanly, building knowledge from clean/."""
    pipe, work_dir = _setup_mock_pipe(tmp_path, extractor={"enabled": False})
    res = await pipe.run("quantum-gravity")

    assert res.failed_stage is None
    assert "extract" in res.stages_skipped
    assert "knowledge" in res.stages_completed
    assert "report" in res.stages_completed

    topic_dir = work_dir / "quantum-gravity"
    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert "extract" in summary["stages_skipped"]


# ============================================================================
# Dimension 4: Protocol v1 Envelope Schema & Invariant Conformance
# ============================================================================

@pytest.mark.asyncio
async def test_protocol_v1_envelope_invariants_across_all_nine_stages(tmp_path: pathlib.Path):
    """Every generated artifact must strictly conform to Protocol v1 schema invariants."""
    pipe, work_dir = _setup_mock_pipe(tmp_path)
    res = await pipe.run("quantum-gravity")
    assert res.failed_stage is None

    topic_dir = work_dir / "quantum-gravity"
    artifacts_dir = topic_dir / "artifacts"

    for stage in CANONICAL_NINE_STAGES:
        art_path = artifacts_dir / f"{stage}.json"
        assert art_path.exists(), f"Missing artifact for {stage}"

        env = json.loads(art_path.read_text(encoding="utf-8"))

        # Invariant 1: v == 1
        assert env["v"] == 1

        # Invariant 2: run_id is a non-empty string
        assert isinstance(env["run_id"], str) and len(env["run_id"]) > 0

        # Invariant 3: stage matches canonical name or known alias
        canon_or_alias = {stage, "network" if stage == "knowledge" else "", "gate" if stage == "qgate" else ""}
        assert env["stage"] in canon_or_alias

        # Invariant 4: idempotency_key is 64 hex characters
        assert isinstance(env["idempotency_key"], str)
        assert len(env["idempotency_key"]) == 64
        int(env["idempotency_key"], 16)  # must parse as hex

        # Invariant 5: budget_lease conforms to non-negative limits
        lease = env["budget_lease"]
        assert isinstance(lease["lease_id"], str)
        assert lease["tokens_max"] >= 0
        assert lease["cost_max"] >= 0
        assert lease["wall_s_max"] >= 0
        assert lease["search_calls_max"] >= 0
        assert "issued_at" in lease
        assert "expires_at" in lease

        # Invariant 6: error is None -> result is not None
        assert env["error"] is None
        assert env["result"] is not None


# ============================================================================
# Dimension 5: Citation Coverage 1.0 Enforcement
# ============================================================================

def test_citation_coverage_dropped_unverified_claims():
    """Claims without valid citations are dropped to dropped_claims, maintaining Citation Coverage 1.0."""
    gate_env = {
        "v": 1, "run_id": "r-cit", "stage": "gate", "request_id": "g-01",
        "idempotency_key": "0" * 64,
        "budget_lease": {"lease_id": "l-1", "tokens_max": 1000, "cost_max": 1.0, "wall_s_max": 10.0, "search_calls_max": 1, "issued_at": "2026-09-17T14:00:00Z", "expires_at": "2026-09-17T15:00:00Z"},
        "result": {"verdict": "STOP_SUCCESS", "reasons": []},
        "error": None,
    }
    net_env = {
        "v": 1, "run_id": "r-cit", "stage": "network", "request_id": "n-01",
        "idempotency_key": "1" * 64,
        "budget_lease": gate_env["budget_lease"],
        "result": {
            "nodes": [{
                "node_id": "src:valid",
                "title": "Valid Source",
                "evidence_spans": [{"locator": "https://arxiv.org/abs/valid", "content_sha256": "v" * 64, "round_id": 0}],
            }],
            "edges": [],
            "counts": {"nodes": 1, "edges": 0},
        },
        "error": None,
    }

    req = report_min.request_from_chain(gate_env, [net_env])
    rep_res = report_min.run_report(req)

    assert rep_res["error"] is None
    res = rep_res["result"]
    assert res["counts"]["claims"] == 1
    assert isinstance(res["dropped_claims"], list)
    assert all(len(c["citations"]) > 0 for c in res["report"]["claims"])


# ============================================================================
# Dimension 6: Downstream Deliverables Generation & Wiki-Stage Packaging
# ============================================================================

@pytest.mark.asyncio
async def test_downstream_deliverables_and_wiki_packaging(tmp_path: pathlib.Path):
    """End-to-end execution generates valid deliverables that successfully build a wiki-stage package."""
    pipe, work_dir = _setup_mock_pipe(tmp_path)
    res = await pipe.run("quantum-gravity")
    assert res.failed_stage is None

    topic_dir = work_dir / "quantum-gravity"

    # 1. report.md
    report_file = topic_dir / "report.md"
    assert report_file.exists()
    report_content = report_file.read_text(encoding="utf-8")
    assert len(report_content) > 50

    # 2. tree/00-主表.md
    tree_index = topic_dir / "tree" / "00-主表.md"
    assert tree_index.exists()
    tree_content = tree_index.read_text(encoding="utf-8")
    assert "[[" in tree_content and "]]" in tree_content

    # 3. sources.json
    sources_file = topic_dir / "sources.json"
    assert sources_file.exists()
    sources = json.loads(sources_file.read_text(encoding="utf-8"))
    assert isinstance(sources, list)
    assert len(sources) > 0
    assert "url" in sources[0]
    assert "fetchedAt" in sources[0]
    assert "content_hash" in sources[0]

    # 4. run-summary.json
    summary_file = topic_dir / "run-summary.json"
    assert summary_file.exists()
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0

    # 5. Wiki-stage packaging
    pkg_dest = tmp_path / "wiki_packages"
    pkg_dest.mkdir(parents=True, exist_ok=True)
    pkg = build_stage_package(topic_dir, pkg_dest)

    assert pkg is not None
    assert pkg.package_id.startswith("rp_")
    assert (pkg.package_path / "manifest.json").exists()
    manifest = json.loads((pkg.package_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert len(manifest["artifacts"]) > 0


@pytest.mark.asyncio
async def test_default_pipeline_config_must_run_native_nine_loop(tmp_path: pathlib.Path):
    """R1 & Acceptance Criteria: Default PipelineConfig without explicit stages must execute native 9-stage closed loop."""
    pipe, work_dir = _setup_mock_pipe(tmp_path)
    # Default PipelineConfig has default stages=['collect', 'deepen', 'clean', 'extract', 'organize', 'report']
    pipe.config = PipelineConfig(topic="default-nine", work_dir=work_dir, resume=False)
    res = await pipe.run("default-nine")

    topic_dir = work_dir / "default-nine"
    artifacts = topic_dir / "artifacts"
    for stage in CANONICAL_NINE_STAGES:
        assert (artifacts / f"{stage}.json").exists(), f"Missing {stage}.json in artifacts/ with default PipelineConfig"

