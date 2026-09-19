"""Unit tests for atomic ChainState, CAS merge semantics, and crash/resume verification.

Tests Protocol v1 envelope persistence, SHA-256 integrity verification,
canonical stage aliases, and atomic write resilience.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.chain_state import (
    ChainState,
    ChainStateFault,
    E_CAS_CONFLICT,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    sha256_bytes,
    write_atomic,
)
from research_tool.nine_loop import merge_min


def _make_dummy_envelope(stage: str, run_id: str = "run-test-01", idempotency_key: str = "k" * 64) -> dict:
    return {
        "v": 1,
        "run_id": run_id,
        "stage": stage,
        "request_id": f"req:{stage}:001",
        "idempotency_key": idempotency_key,
        "budget_lease": {
            "lease_id": "lease-001",
            "tokens_max": 10000,
            "cost_max": 1.0,
            "wall_s_max": 30.0,
            "search_calls_max": 5,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
        "result": {"status": "ok", "items": [1, 2, 3]},
        "error": None,
    }


def test_atomic_commit_leaves_no_tmp_files(tmp_path: Path):
    """Verify write_atomic and commit_stage leave zero dangling .tmp-* files."""
    cs = ChainState(tmp_path)
    input_key = "a" * 64
    state = cs.load(input_key)

    env = _make_dummy_envelope("collect", idempotency_key=input_key)
    cs.commit_stage(state, "collect", env)

    assert (tmp_path / "artifacts" / "collect.json").exists()
    assert (tmp_path / "state.json").exists()

    # Zero tmp files in workspace or artifacts
    assert list(tmp_path.glob("*.tmp-*")) == []
    assert list((tmp_path / "artifacts").glob("*.tmp-*")) == []


def test_sha256_verification_detects_tamper(tmp_path: Path):
    """Verify that disk tampering of a committed artifact is detected by verify_state and read_stage."""
    cs = ChainState(tmp_path)
    input_key = "b" * 64
    state = cs.load(input_key)

    env = _make_dummy_envelope("clean", idempotency_key=input_key)
    cs.commit_stage(state, "clean", env)

    # Corrupt artifact file
    art_path = tmp_path / "artifacts" / "clean.json"
    art_path.write_bytes(b'{"tampered": true}')

    with pytest.raises(ChainStateFault) as exc_info:
        cs.read_stage(state, "clean")
    assert exc_info.value.code == E_STATE

    # Also detected during load with resume=True
    cs_resumed = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc_info2:
        cs_resumed.load(input_key, resume=True)
    assert exc_info2.value.code == E_STATE


def test_idempotency_conflict_rejects_mismatched_key(tmp_path: Path):
    """Verify loading with a mismatched input idempotency key raises E_IDEMPOTENCY_CONFLICT."""
    cs = ChainState(tmp_path)
    key1 = "1" * 64
    key2 = "2" * 64
    state = cs.load(key1)
    env = _make_dummy_envelope("collect", idempotency_key=key1)
    cs.commit_stage(state, "collect", env)

    cs2 = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc_info:
        cs2.load(key2, resume=True)
    assert exc_info.value.code == E_IDEMPOTENCY_CONFLICT


def test_stage_aliases_are_interchangeable(tmp_path: Path):
    """Verify knowledge <-> network and qgate <-> gate can be committed and read interchangeably."""
    cs = ChainState(tmp_path)
    key = "c" * 64
    state = cs.load(key)

    # Commit knowledge -> should mirror to network.json
    know_env = _make_dummy_envelope("knowledge", idempotency_key=key)
    cs.commit_stage(state, "knowledge", know_env)

    assert (tmp_path / "artifacts" / "knowledge.json").exists()
    assert (tmp_path / "artifacts" / "network.json").exists()

    # Read back via network alias
    net_read = cs.read_stage(state, "network")
    assert net_read["stage"] == "knowledge"

    # Commit gate -> should mirror to qgate.json
    gate_env = _make_dummy_envelope("gate", idempotency_key=key)
    cs.commit_stage(state, "gate", gate_env)

    assert (tmp_path / "artifacts" / "gate.json").exists()
    assert (tmp_path / "artifacts" / "qgate.json").exists()

    # Read back via qgate alias
    qgate_read = cs.read_stage(state, "qgate")
    assert qgate_read["stage"] == "gate"


def test_cas_merge_success_and_conflict_handling(tmp_path: Path):
    """Verify CAS merge updates network digest on match and detects conflict on mismatch."""
    cs = ChainState(tmp_path)
    key = "d" * 64
    state = cs.load(key)

    # Initial network graph
    initial_graph = {
        "nodes": [
            {
                "node_id": "src:1",
                "evidence_spans": [{"locator": "https://example.com/1", "content_sha256": "1" * 64, "round_id": 0}],
            }
        ],
        "edges": [],
    }
    lease = {
        "lease_id": "lease-001",
        "tokens_max": 10000,
        "cost_max": 1.0,
        "wall_s_max": 30.0,
        "search_calls_max": 5,
        "issued_at": "2026-09-17T14:00:00Z",
        "expires_at": "2026-09-17T15:00:00Z",
    }
    net_env = {
        "v": 1,
        "run_id": "run-cas",
        "stage": "network",
        "request_id": "net:0",
        "idempotency_key": merge_min.graph_digest(initial_graph),
        "budget_lease": lease,
        "result": initial_graph,
        "error": None,
    }
    cs.commit_stage(state, "network", net_env)

    # 1. Matching CAS merge
    prev_digest = merge_min.graph_digest(initial_graph)
    responses = [
        {
            "request_id": "tq:001",
            "facts": [
                {
                    "source_id": "src:2",
                    "locator": "https://example.com/2",
                    "content_sha256": "2" * 64,
                }
            ],
        }
    ]
    merge_req = merge_min.request_from_chain(net_env, responses, prev_digest=prev_digest)
    merge_env = merge_min.run_merge(merge_req)

    # Commit loop merge
    merged_net_env = {
        "v": 1,
        "run_id": "run-cas",
        "stage": "network",
        "request_id": "net:1",
        "idempotency_key": merge_env["result"]["latest_digest"],
        "budget_lease": lease,
        "result": merge_env["result"]["graph"],
        "error": None,
    }
    state = cs.commit_loop_merge(state, merged_net_env, merge_env, round_idx=0)

    assert state["loop"]["current_round"] == 1
    assert state["loop"]["latest_network_digest"] == merge_env["result"]["latest_digest"]
    assert len(state["loop"]["history"]) == 1

    # 2. Mismatched CAS merge -> E_CAS_CONFLICT
    stale_digest = "0" * 64
    stale_merge_req = merge_min.request_from_chain(merged_net_env, responses, prev_digest=stale_digest)
    stale_env = merge_min.run_merge(stale_merge_req)
    assert stale_env["error"] is not None
    assert stale_env["error"]["code"] == E_CAS_CONFLICT

    with pytest.raises(merge_min.MergeStageFault) as exc_info:
        merge_min.merge(merged_net_env["result"], responses, prev_digest=stale_digest)
    assert exc_info.value.frame["code"] == E_CAS_CONFLICT


def test_crash_resume_partial_execution(tmp_path: Path):
    """Verify that a partially completed pipeline can resume without re-running done stages."""
    cs = ChainState(tmp_path)
    key = "e" * 64
    state = cs.load(key)

    # Commit collect and clean
    env_c = _make_dummy_envelope("collect", idempotency_key=key)
    cs.commit_stage(state, "collect", env_c)

    env_cl = _make_dummy_envelope("clean", idempotency_key=key)
    cs.commit_stage(state, "clean", env_cl)

    # Simulate fresh process resuming
    cs_resume = ChainState(tmp_path)
    resumed_state = cs_resume.load(key, resume=True)

    assert cs_resume.is_stage_complete(resumed_state, "collect") is True
    assert cs_resume.is_stage_complete(resumed_state, "clean") is True
    assert cs_resume.is_stage_complete(resumed_state, "extract") is False
    assert resumed_state["done"] == ["collect", "clean"]


def test_cleanup_tmps_removes_stale_files(tmp_path: Path):
    """Verify cleanup_tmps removes leftover .tmp-* files in work_dir and artifacts."""
    cs = ChainState(tmp_path)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)

    stale1 = tmp_path / "state.json.tmp-1234-abcd"
    stale1.write_text("partial", encoding="utf-8")
    stale2 = tmp_path / "artifacts" / "clean.json.tmp-5678-ef01"
    stale2.write_text("partial", encoding="utf-8")

    removed = cs.cleanup_tmps()
    assert removed == 2
    assert not stale1.exists()
    assert not stale2.exists()


@pytest.mark.asyncio
async def test_pipeline_native_nine_loop_e2e_materializes_deliverables(tmp_path: Path, monkeypatch):
    """F01 & F05: Verify ResearchPipeline natively runs 9 stages and writes all contract deliverables."""
    from research_tool.application.pipeline import ResearchPipeline, CANONICAL_NINE_STAGES
    from research_tool.domain.models import PipelineConfig
    from research_tool.infrastructure.llm import MockLLMClient

    cfg = PipelineConfig(
        topic="quantum-sim",
        work_dir=tmp_path / "out",
        stages=["collect", "clean", "extract", "organize", "report"],
        resume=False,
    )
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient(chat_response="quantum report"))

    async def fake_exec(stage, topic, topic_dir, result):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text("quantum content", encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("clean quantum content", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text("[]", encoding="utf-8")
        elif stage == "organize":
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络", encoding="utf-8")

    monkeypatch.setattr(pipe, "_exec", fake_exec)

    result = await pipe.run("quantum-sim")

    assert result.failed_stage is None
    for s in CANONICAL_NINE_STAGES:
        assert s in result.stages_completed

    topic_dir = result.topic_dir
    artifacts = topic_dir / "artifacts"
    assert artifacts.is_dir()

    # 1. Envelopes
    for stage in ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]:
        env_file = artifacts / f"{stage}.json"
        assert env_file.exists(), f"missing envelope {stage}.json"
        data = json.loads(env_file.read_text(encoding="utf-8"))
        assert data["v"] == 1
        assert "budget_lease" in data

    # 2. state.json & SHA-256 verification
    state_file = topic_dir / "state.json"
    assert state_file.exists()
    cs = ChainState(topic_dir)
    loaded_state = cs.load(json.loads(state_file.read_text())["input_idempotency_key"], resume=True)
    assert loaded_state["stages"]["collect"] is not None

    # 3. Downstream files
    assert (topic_dir / "report.md").exists()
    assert (topic_dir / "tree" / "00-主表.md").exists()
    assert (topic_dir / "sources.json").exists()
    assert (topic_dir / "run-summary.json").exists()
    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0

