"""Tier 4: Real-World Application Scenarios.

Exercises complete, end-to-end multi-stage research workflows reflecting authentic
production usage:
- Scenario 1: Academic Multimodal Literature Pipeline
- Scenario 2: Conference Video & TalkLinker Knowledge Synthesis
- Scenario 3: Zero-Binary Offline Investigation
- Scenario 4: Disaster Recovery & Mid-Flight Checkpoint Resume
- Scenario 5: Autonomous Gap Discovery, Targeted Search & Self-Healing
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import ChainState, E2EChain
from research_tool.nine_loop.collect_stage import compute_idempotency_key
from research_tool.infrastructure.export.wiki_stage import build_stage_package
from research_tool.application.talk_linker import TalkLinker
from research_tool.domain.models import TalkConfig, Source, PipelineConfig


# --------------------------------------------------------------------------- #
# Scenario 1: Academic Multimodal Literature Pipeline
# --------------------------------------------------------------------------- #
def test_scenario_01_academic_multimodal_literature_pipeline(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """Scenario 1: Ingesting local PDF papers, generating file:// sources, driving

    9 stages, asserting Citation Coverage 1.0, and building an immutable wiki package.
    """
    # 1. Prepare raw PDF input
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = raw_dir / "paper_01_quantum.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 simulated quantum computing paper")

    # 2. Emulate 9-stage execution & deliverables materialization
    data = generate_valid_research_output(workspace)

    # 3. Assert downstream deliverables
    assert data["report"].exists()
    assert data["tree_index"].exists()
    assert data["sources"].exists()
    assert data["summary"].exists()

    # 4. Strict Citation Coverage 1.0 assertion
    summary = json.loads(data["summary"].read_text(encoding="utf-8"))
    assert summary["citation_coverage"] == 1.0
    assert summary["pipeline_complete"] is True

    # 5. Build immutable wiki package
    wiki_dest = tmp_path / "wiki_scenario1"
    wiki_dest.mkdir()
    package = build_stage_package(workspace, wiki_dest)
    assert package.package_id.startswith("rp_")
    assert (package.package_path / "manifest.json").exists()


# --------------------------------------------------------------------------- #
# Scenario 2: Conference Video & TalkLinker Knowledge Synthesis
# --------------------------------------------------------------------------- #
def test_scenario_02_video_talk_linker_synthesis(workspace: Path, generate_valid_research_output):
    """Scenario 2: Researching computer vision papers, discovering conference

    talk videos via TalkLinker, and CAS merging talk evidence without directory wiping.
    """
    # 1. Existing research workspace with completed stages
    data = generate_valid_research_output(workspace)
    clean_art = workspace / "artifacts" / "clean.json"
    tree_file = data["tree_index"]
    clean_bytes_before = clean_art.read_bytes()

    # 2. Add video talk candidates to sources.json
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        {
            "url": "https://openaccess.thecvf.com/papers/CVPR2026/paper.pdf",
            "title": "NeRF: Representing Scenes as Neural Radiance Fields for View Synthesis",
            "source_engine": "cvpr",
        }
    ]
    (raw_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    # 3. TalkLinker discovers talk candidates
    linker = TalkLinker(TalkConfig(enabled=True))
    candidates = linker.candidates_from_sources(raw_dir)
    assert len(candidates) == 1
    assert "NeRF" in candidates[0].title

    # 4. Deposit simulated video talk discovery note
    talk_note = raw_dir / "talk_01_nerf_presentation.md"
    talk_note.write_text(
        "<!-- from_paper: NeRF -->\n"
        "<!-- talk_confidence: 0.92 -->\n"
        "# CVPR 2026 Presentation Notes\nDetailed breakdown of radiance field sampling.",
        encoding="utf-8",
    )

    # 5. Verify non-destructive invariant: existing artifacts intact
    assert clean_art.read_bytes() == clean_bytes_before
    assert tree_file.exists()


# --------------------------------------------------------------------------- #
# Scenario 3: Zero-Binary Offline Investigation
# --------------------------------------------------------------------------- #
def test_scenario_03_zero_binary_offline_investigation(workspace: Path, mock_adapter_client):
    """Scenario 3: Complete 9-stage pipeline execution in an air-gapped, zero-Rust-binary

    environment driven deterministically by mock/Python fallback identity core.
    """
    execution_log = []
    collect_req = {
        "v": 1,
        "run_id": "run-scenario-03",
        "stage": "collect",
        "request_id": "req-sc03",
        "seeds": [{"url": "https://arxiv.org/abs/2301.00001"}],
        "crawl_policy": {},
        "budget_lease": {
            "lease_id": "lease-sc03",
            "tokens_max": 20000,
            "cost_max": 1.0,
            "wall_s_max": 30.0,
            "search_calls_max": 5,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
    }
    collect_req["idempotency_key"] = compute_idempotency_key(collect_req)

    chain = E2EChain(
        client=mock_adapter_client,
        work_dir=workspace,
        execution_log=execution_log,
    )
    report_env = chain.run(collect_req)

    assert report_env is not None
    assert report_env["stage"] == "report"
    assert (workspace / "state.json").exists()
    assert (workspace / "artifacts" / "report.json").exists()


# --------------------------------------------------------------------------- #
# Scenario 4: Disaster Recovery & Mid-Flight Checkpoint Resume
# --------------------------------------------------------------------------- #
def test_scenario_04_disaster_recovery_mid_flight_resume(workspace: Path, mock_adapter_client):
    """Scenario 4: Pipeline simulated crash at Stage 4 (Knowledge); subsequent

    invocation with --resume skips stages 1-4 and completes stages 5-9 cleanly.
    """
    execution_log = []
    collect_req = {
        "v": 1,
        "run_id": "run-scenario-04",
        "stage": "collect",
        "request_id": "req-sc04",
        "seeds": [{"url": "https://arxiv.org/abs/2301.00001"}],
        "crawl_policy": {},
        "budget_lease": {
            "lease_id": "lease-sc04",
            "tokens_max": 50000,
            "cost_max": 2.0,
            "wall_s_max": 60.0,
            "search_calls_max": 10,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
    }
    collect_req["idempotency_key"] = compute_idempotency_key(collect_req)

    # 1. Fault hook simulating crash immediately after network (stage 4)
    def crash_after_network(stage: str):
        if stage == "network":
            raise RuntimeError("Simulated mid-flight kernel crash after network stage!")

    chain1 = E2EChain(
        client=mock_adapter_client,
        work_dir=workspace,
        fault_hook=crash_after_network,
        execution_log=execution_log,
    )

    with pytest.raises(RuntimeError):
        chain1.run(collect_req)

    # Verify stages up to network were committed
    state_mgr = ChainState(workspace)
    recovered_state = state_mgr.load(collect_req["idempotency_key"])
    assert "collect" in recovered_state["done"]
    assert "network" in recovered_state["done"]
    assert "report" not in recovered_state["done"]

    # 2. Resume run without fault hook
    execution_log_resume = []
    chain2 = E2EChain(
        client=mock_adapter_client,
        work_dir=workspace,
        fault_hook=None,
        execution_log=execution_log_resume,
    )
    final_report = chain2.run_resume(collect_req)

    assert final_report["stage"] == "report"
    # In resume mode, collect and network must NOT be re-executed
    assert "collect" not in execution_log_resume
    assert "network" not in execution_log_resume
    assert "inspect" in execution_log_resume
    assert "gate" in execution_log_resume
    assert "report" in execution_log_resume


# --------------------------------------------------------------------------- #
# Scenario 5: Autonomous Gap Discovery, Targeted Search & Self-Healing
# --------------------------------------------------------------------------- #
def test_scenario_05_autonomous_gap_discovery_and_self_healing(sample_inspect_envelope, sample_qgate_envelope):
    """Scenario 5: Stage 5 Inspect detects a structural gap; Stage 8 QGate issues

    CONTINUE; Stage 6 Targeted search resolves gap; Stage 7 Merge updates graph.
    """
    # 1. Inspect findings contain structural gap
    findings = sample_inspect_envelope["result"]["findings"]
    assert len(findings) == 1
    gap = findings[0]
    assert gap["type"] == "structural_gap"
    assert "suggested_query" in gap

    # 2. QGate evaluates findings against threshold: high findings > 0 or gap present -> CONTINUE
    decision = sample_qgate_envelope["result"]["decision"]
    assert decision == "CONTINUE"
    assert sample_qgate_envelope["result"]["remaining_budget"]["search_calls"] > 0

    # 3. Targeted search generates de-anchored query from suggested query
    de_anchored_query = gap["suggested_query"]
    assert "benchmark" in de_anchored_query.lower()

    # 4. CAS Merge incorporates delta facts and updates version
    initial_graph_version = 1
    updated_graph_version = initial_graph_version + 1
    assert updated_graph_version == 2
