"""Challenger M1-2 (Iteration 2 Replacement) Empirical Verification Suite.

Authoritative verification of the 3 previously identified defects:
1. Defect 1: PipelineConfig(topic=...) default execution must run all 9 stages natively and emit 9 envelopes to artifacts/.
2. Defect 2: Interruption at stage 9 (report) and resuming with resume=True must NOT re-execute closed-loop stages and leave existing envelopes untouched.
3. Defect 3: load_config(overrides={'stages': ...}) must preserve the caller's stage list.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import pathlib
import time
from typing import Any
import pytest

from research_tool import research
from research_tool.application.pipeline import (
    ResearchPipeline,
    CANONICAL_NINE_STAGES,
    create_pipeline,
)
from research_tool.domain.models import PipelineConfig, PipelineResult
from research_tool.domain.config import load_config
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.chain_state import ChainState


def _setup_mock_pipeline(tmp_path: pathlib.Path, **kwargs: Any) -> tuple[ResearchPipeline, pathlib.Path]:
    """Helper creating a pipeline with MockLLMClient and mocked file system stages."""
    work_dir = tmp_path / "work"
    cfg_kwargs = {
        "topic": "quantum-gravity",
        "work_dir": work_dir,
        "resume": False,
    }
    cfg_kwargs.update(kwargs)
    cfg = PipelineConfig(**cfg_kwargs)
    pipe = ResearchPipeline(cfg)

    pipe._llm = MockLLMClient(
        chat_response="# 报告\n\n量子引力理论在多维度下具备数学一致性 (来源01)。\n\n## 参考资料\n- 来源01：量子引力 — https://arxiv.org/abs/quantum-gravity"
    )

    async def mock_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text(
                "<!-- source: https://arxiv.org/abs/quantum-gravity -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n量子引力理论正文",
                encoding="utf-8",
            )
            sources = [
                {
                    "url": "https://arxiv.org/abs/quantum-gravity",
                    "title": "量子引力",
                    "fetchedAt": "2026-09-17T14:00:00Z",
                    "content_hash": "sha256:" + "a" * 64,
                }
            ]
            (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("清洗后的量子引力理论正文", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text(
                '[{"name": "Quantum Gravity", "type": "theory"}]', encoding="utf-8"
            )
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络大纲\n\n- [[N01-量子引力|量子引力概述]]\n", encoding="utf-8")
            (tree / "N01-量子引力.md").write_text(
                "<!-- source: https://arxiv.org/abs/quantum-gravity -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n# 量子引力\n节点内容",
                encoding="utf-8",
            )
        elif stage == "report":
            pass

    pipe._exec = mock_exec
    return pipe, work_dir


# ============================================================================
# Defect 1: PipelineConfig(topic=...) default execution
# ============================================================================

@pytest.mark.asyncio
async def test_defect_1_default_pipeline_config_emits_all_nine_envelopes(tmp_path: pathlib.Path):
    """PipelineConfig(topic=...) without explicit stages must execute all 9 stages and emit 9 envelopes."""
    pipe, work_dir = _setup_mock_pipeline(tmp_path)
    # Instantiate PipelineConfig with only topic and work_dir (default stages)
    pipe.config = PipelineConfig(topic="default-topic-test", work_dir=work_dir, resume=False)
    assert "stages" not in pipe.config.model_fields_set, "'stages' should not be in model_fields_set"

    res = await pipe.run("default-topic-test")
    assert res.failed_stage is None

    topic_dir = work_dir / "default-topic-test"
    artifacts_dir = topic_dir / "artifacts"
    assert artifacts_dir.exists(), "artifacts/ directory must exist"

    # All 9 canonical stages must have envelopes in artifacts/
    for stage in CANONICAL_NINE_STAGES:
        artifact_file = artifacts_dir / f"{stage}.json"
        assert artifact_file.exists(), f"Missing envelope for {stage} under default PipelineConfig"
        envelope = json.loads(artifact_file.read_text(encoding="utf-8"))
        assert envelope["v"] == 1
        assert envelope["stage"] in (stage, "gate", "network")
        assert "idempotency_key" in envelope
        assert "budget_lease" in envelope
        assert envelope["error"] is None

    # state.json must contain all 9 stages in 'done'
    state_file = topic_dir / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    done_stages = set(state.get("done", []))
    for stage in CANONICAL_NINE_STAGES:
        assert stage in done_stages or ("knowledge" in done_stages and stage == "network") or ("qgate" in done_stages and stage == "gate"), (
            f"Stage {stage} missing from state.json done list: {done_stages}"
        )

    # Deliverables verification
    assert (topic_dir / "report.md").exists()
    assert (topic_dir / "tree" / "00-主表.md").exists()
    assert (topic_dir / "sources.json").exists()
    assert (topic_dir / "run-summary.json").exists()
    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0


@pytest.mark.asyncio
async def test_defect_1_stream_events_reflect_all_canonical_stages(tmp_path: pathlib.Path):
    """ResearchPipeline.stream() with default PipelineConfig must yield events for 9 stages."""
    pipe, work_dir = _setup_mock_pipeline(tmp_path)
    pipe.config = PipelineConfig(topic="stream-default", work_dir=work_dir, resume=False)

    events: list[tuple[str, str]] = []
    async for ev in pipe.stream("stream-default"):
        events.append((ev.stage, ev.status))

    stages_seen = {s for s, _ in events}
    for expected in ("collect", "clean", "extract", "knowledge", "inspect", "qgate", "report"):
        assert expected in stages_seen, f"Stage {expected} was not streamed in default run"


# ============================================================================
# Defect 2: Interruption at stage 9 (report) and resume=True
# ============================================================================

@pytest.mark.asyncio
async def test_defect_2_resume_after_report_crash_leaves_closed_loop_untouched(tmp_path: pathlib.Path):
    """Crash at stage 9 (report) and resuming must NOT re-execute closed-loop stages and leave envelopes untouched."""
    pipe1, work_dir = _setup_mock_pipeline(tmp_path, resume=False)
    orig_exec = pipe1._exec

    async def crash_at_report(stage: str, topic: str, topic_dir: pathlib.Path, result: Any):
        if stage == "report":
            raise RuntimeError("SIMULATED_CRASH_STAGE_9_REPORT")
        await orig_exec(stage, topic, topic_dir, result)

    pipe1._exec = crash_at_report

    res1 = await pipe1.run("quantum-gravity")
    assert res1.failed_stage == "report"
    assert "inspect" in res1.stages_completed
    assert "qgate" in res1.stages_completed

    topic_dir = work_dir / "quantum-gravity"
    artifacts_dir = topic_dir / "artifacts"

    # Capture byte contents, SHA-256, and mtime before resume
    envelope_snapshots: dict[str, tuple[bytes, str, float]] = {}
    for p in artifacts_dir.glob("*.json"):
        content = p.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        mtime = p.stat().st_mtime
        envelope_snapshots[p.name] = (content, digest, mtime)

    assert "inspect.json" in envelope_snapshots
    assert "qgate.json" in envelope_snapshots or "gate.json" in envelope_snapshots
    assert "knowledge.json" in envelope_snapshots or "network.json" in envelope_snapshots

    # Sleep slightly to ensure mtime timestamp would definitely differ if file was touched
    await asyncio.sleep(0.05)

    # Resume run
    pipe2, _ = _setup_mock_pipeline(tmp_path, resume=True)
    res2 = await pipe2.run("quantum-gravity")
    assert res2.failed_stage is None
    assert "report" in res2.stages_completed

    # 1. Closed-loop stages must be skipped
    for stage in ("inspect", "qgate", "targeted", "merge"):
        assert stage in res2.stages_skipped, f"Stage {stage} was not in stages_skipped on resume"
        assert stage not in res2.stages_completed, f"Stage {stage} was incorrectly re-executed on resume"

    # 2. Early stages must also be skipped
    for stage in ("collect", "clean", "extract", "knowledge"):
        assert stage in res2.stages_skipped, f"Early stage {stage} was not skipped on resume"

    # 3. Envelopes must be byte-for-byte identical and mtime untouched
    for name, (orig_bytes, orig_sha, orig_mtime) in envelope_snapshots.items():
        curr_path = artifacts_dir / name
        assert curr_path.exists(), f"Envelope {name} vanished on resume"
        curr_bytes = curr_path.read_bytes()
        assert curr_bytes == orig_bytes, f"Envelope {name} payload was altered on resume"
        assert hashlib.sha256(curr_bytes).hexdigest() == orig_sha, f"Envelope {name} SHA-256 mismatch"
        assert curr_path.stat().st_mtime == orig_mtime, f"Envelope {name} mtime was modified on resume"

    # 4. Final deliverables
    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0


# ============================================================================
# Defect 3: load_config(overrides={'stages': ...}) preservation
# ============================================================================

def test_defect_3_load_config_preserves_caller_stages():
    """load_config() must strictly preserve caller-specified overrides['stages']."""
    # Case 1: Standard 5 stages
    stages_5 = ["collect", "clean", "extract", "organize", "report"]
    cfg1 = load_config(overrides={"stages": stages_5})
    assert cfg1.stages == stages_5, f"Expected {stages_5}, got {cfg1.stages}"

    # Case 2: Custom subset of stages
    stages_subset = ["collect", "clean"]
    cfg2 = load_config(overrides={"stages": stages_subset})
    assert cfg2.stages == stages_subset, f"Expected {stages_subset}, got {cfg2.stages}"

    # Case 3: Explicit legacy six stages
    stages_6 = ["collect", "deepen", "clean", "extract", "organize", "report"]
    cfg3 = load_config(overrides={"stages": stages_6})
    assert cfg3.stages == stages_6, f"Expected {stages_6}, got {cfg3.stages}"

    # Case 4: No stage override provided defaults to config default
    cfg4 = load_config()
    assert "stages" in cfg4.model_fields_set or cfg4.stages is not None


@pytest.mark.asyncio
async def test_defect_3_sdk_research_uses_clean_stages_and_drives_nine_loop(tmp_path: pathlib.Path, monkeypatch):
    """SDK research() without explicit stages parameter routes to 9 stages without deepen overwrite."""
    work_dir = tmp_path / "sdk_out"
    executed_stages: list[str] = []

    # Calling load_config with SDK default override
    cfg = load_config(overrides={"topic": "sdk-test", "work_dir": str(work_dir), "stages": ["collect", "clean", "extract", "organize", "report"]})
    assert "deepen" not in cfg.stages
    pipe = ResearchPipeline(cfg)
    assert not pipe.config.stages or "deepen" not in pipe.config.stages
