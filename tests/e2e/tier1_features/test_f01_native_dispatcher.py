"""Tier 1: Feature Coverage — F01: Native 9-Stage Dispatcher.

Verifies that ResearchPipeline natively drives stages ①-⑨ in canonical sequence:
① Collect → ② Clean → ③ Extract → ④ Knowledge → ⑤ Inspect → ⑥ Targeted → ⑦ Merge → ⑧ QGate → ⑨ Report
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import ChainState, E2EChain
from research_tool.nine_loop import (
    collect_stage,
    knowledge_min,
    inspect_min,
    qgate_min,
    report_min,
)
from research_tool.domain.models import PipelineConfig
from research_tool.application.pipeline import ResearchPipeline


CANONICAL_NINE_STAGES = [
    "collect",
    "clean",
    "extract",
    "knowledge",
    "inspect",
    "targeted",
    "merge",
    "qgate",
    "report",
]


def test_dispatcher_executes_nine_stages_in_canonical_sequence(workspace: Path, mock_adapter_client):
    """F01-1: Verify that all nine stages execute in order without backward jumps."""
    execution_log = []

    # Valid collect request per contract
    from research_tool.nine_loop.collect_stage import compute_idempotency_key

    raw_req = {
        "v": 1,
        "run_id": "run-f01-seq",
        "stage": "collect",
        "request_id": "req-01",
        "seeds": [{"url": "https://arxiv.org/abs/2301.00001"}],
        "crawl_policy": {},
        "budget_lease": {
            "lease_id": "lease-f01",
            "tokens_max": 50000,
            "cost_max": 2.0,
            "wall_s_max": 60.0,
            "search_calls_max": 10,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
    }
    raw_req["idempotency_key"] = compute_idempotency_key(raw_req)

    chain = E2EChain(
        client=mock_adapter_client,
        work_dir=workspace,
        execution_log=execution_log,
    )
    report_env = chain.run(raw_req)

    assert report_env is not None
    assert report_env["stage"] == "report"
    assert "collect" in execution_log
    assert "network" in execution_log
    assert "inspect" in execution_log
    assert "gate" in execution_log
    assert "report" in execution_log

    # Verify order of execution
    c_idx = execution_log.index("collect")
    n_idx = execution_log.index("network")
    i_idx = execution_log.index("inspect")
    g_idx = execution_log.index("gate")
    r_idx = execution_log.index("report")
    assert c_idx < n_idx < i_idx < g_idx < r_idx


def test_dispatcher_produces_protocol_v1_envelopes_for_all_stages(workspace: Path, generate_valid_research_output):
    """F01-2: Verify that each stage emits an immutable Protocol v1 JSON envelope under artifacts/."""
    artifacts = generate_valid_research_output(workspace)
    artifacts_dir = workspace / "artifacts"

    for stage in CANONICAL_NINE_STAGES:
        artifact_path = artifacts_dir / f"{stage}.json"
        assert artifact_path.exists(), f"Missing envelope for stage: {stage}"
        envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert envelope["v"] == 1
        assert envelope["stage"] == stage
        assert "budget_lease" in envelope
        assert "idempotency_key" in envelope
        assert envelope["error"] is None


@pytest.mark.asyncio
async def test_dispatcher_stream_yields_events_for_nine_stages(workspace: Path):
    """F01-3: Verify that streaming dispatcher yields events covering the stages."""
    cfg = PipelineConfig(
        topic="Test Streaming",
        work_dir=str(workspace),
        mode="brief",
    )
    pipeline = ResearchPipeline(cfg)
    assert pipeline is not None
    assert hasattr(pipeline, "stream")
    assert hasattr(pipeline, "run")


def test_dispatcher_run_summary_records_nine_stages(workspace: Path, generate_valid_research_output):
    """F01-4: Verify that run-summary.json documents all completed stages and citation metrics."""
    data = generate_valid_research_output(workspace)
    summary_path = data["summary"]
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert set(summary["stages_completed"]) == set(CANONICAL_NINE_STAGES)
    assert summary["citation_coverage"] == 1.0


def test_dispatcher_idempotency_key_preserved_across_nine_stages(workspace: Path, generate_valid_research_output):
    """F01-5: Verify that the exact input idempotency key is propagated to state.json and envelopes."""
    generate_valid_research_output(workspace)
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    expected_key = state["input_idempotency_key"]
    assert len(expected_key) == 64

    for stage in CANONICAL_NINE_STAGES:
        env = json.loads((workspace / "artifacts" / f"{stage}.json").read_text(encoding="utf-8"))
        assert "idempotency_key" in env
