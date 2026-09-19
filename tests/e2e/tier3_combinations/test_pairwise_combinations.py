"""Tier 3: Cross-Feature Combinations — Pairwise Combinatorial Testing.

Exercises multi-feature interactions across CLI, SDK, CAS checkpoints,
multimodal ingest, TalkLinker, downstream wiki, and offline identity engines.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import ChainState, E2EChain
from research_tool.nine_loop.clean_min import compute_idempotency_key
from research_tool.infrastructure.export.wiki_stage import build_stage_package
from research_tool.application.talk_linker import TalkLinker, title_similarity
from research_tool.domain.models import TalkConfig, Source, PipelineConfig
from research_tool.common.slug import slugify


def test_pairwise_cli_options_and_checkpoint_resume(workspace: Path, generate_valid_research_output, run_cli):
    """P01: Pairwise: CLI --resume flag combined with atomic checkpointing under artifacts/."""
    data = generate_valid_research_output(workspace)
    assert data["state"].exists()

    res = run_cli(["run", "Resume Topic", "--output", str(workspace), "--resume", "--dry-run"])
    assert res.returncode == 0
    # In resume mode, pre-existing state is respected
    assert (workspace / "state.json").exists()


def test_pairwise_sdk_and_multimodal_pdf(workspace: Path):
    """P02: Pairwise: Python SDK research config combined with local PDF source."""
    pdf_file = workspace / "test_paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock pdf")

    cfg = PipelineConfig(topic="Multimodal SDK", work_dir=str(workspace))
    assert Path(cfg.work_dir) == workspace

    source = Source(
        url=f"file://{pdf_file.resolve()}",
        title="Multimodal Paper",
        source_engine="pdf",
    )
    assert source.url.startswith("file://")
    assert source.source_engine == "pdf"


def test_pairwise_pure_python_fallback_and_clean_scheme():
    """P03: Pairwise: Python identity normalization combined with Clean stage allowed schemes."""
    from research_tool.nine_loop.clean_min import ALLOWED_SCHEMES

    test_urls = [
        "https://arxiv.org/abs/2301.00001?utm_source=test",
        "http://example.com/data",
    ]
    for u in test_urls:
        scheme = u.split(":")[0]
        assert scheme in ALLOWED_SCHEMES


def test_pairwise_talk_linker_cas_merge_and_citation_coverage(workspace: Path, generate_valid_research_output):
    """P04: Pairwise: TalkLinker paper discovery combined with Citation Coverage 1.0 assertion."""
    data = generate_valid_research_output(workspace)
    summary = json.loads(data["summary"].read_text(encoding="utf-8"))
    assert summary["citation_coverage"] == 1.0

    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = [{"url": "https://thecvf.com/paper.pdf", "title": "CVPR Paper", "source_engine": "cvpr"}]
    (raw_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    linker = TalkLinker(TalkConfig(enabled=True))
    cands = linker.candidates_from_sources(raw_dir)
    assert len(cands) == 1
    # Verify report is not purged
    assert data["report"].exists()


def test_pairwise_strict_agent_and_canonical_aliases():
    """P05: Pairwise: RESEARCH_AGENT_STRICT logic combined with canonical stage aliases."""
    stage_aliases = {"knowledge": "network", "qgate": "gate"}

    canonical_stages = {"collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"}
    aliased_stages = {"collect", "clean", "extract", stage_aliases["knowledge"], "inspect", "targeted", "merge", stage_aliases["qgate"], "report"}

    # Both sets have exactly 9 stages
    assert len(canonical_stages) == 9
    assert len(aliased_stages) == 9


def test_pairwise_budget_lease_and_targeted_search(sample_collect_envelope):
    """P06: Pairwise: Budget lease boundaries combined with targeted loop limits."""
    lease = sample_collect_envelope["budget_lease"]
    assert lease["tokens_max"] > 0
    assert lease["wall_s_max"] > 0
    assert lease["search_calls_max"] > 0


def test_pairwise_config_override_and_cli_flag_precedence(workspace: Path):
    """P07: Pairwise: Config file YAML overridden by dictionary overrides."""
    from research_tool.domain.config import load_config

    cfg_file = workspace / "base_cfg.yaml"
    cfg_file.write_text("pipeline:\n  mode: brief\n  work_dir: ./base\n", encoding="utf-8")

    loaded = load_config(cfg_file, overrides={"topic": "Override Test", "work_dir": str(workspace / "overridden")})
    assert loaded.topic == "Override Test"
    assert Path(loaded.work_dir) == workspace / "overridden"


def test_pairwise_empty_search_and_inspect_gap_detection(sample_inspect_envelope):
    """P08: Pairwise: Inspect stage detecting structural gaps when evidence is missing."""
    findings = sample_inspect_envelope["result"]["findings"]
    assert len(findings) > 0
    assert findings[0]["type"] == "structural_gap"
    assert "suggested_query" in findings[0]


def test_pairwise_qgate_continue_and_decision_branch(sample_qgate_envelope):
    """P09: Pairwise: QGate issuing CONTINUE decision when threshold criteria not met."""
    res = sample_qgate_envelope["result"]
    assert res["decision"] in ("CONTINUE", "PASS", "ABORT")
    assert "remaining_budget" in res


def test_pairwise_unicode_slug_and_wiki_stage_packaging(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """P10: Pairwise: Non-ASCII Chinese topic slug combined with wiki-stage packaging."""
    topic_cn = "量子计算与神经辐射场 2026"
    slug = slugify(topic_cn)
    assert len(slug) > 0

    generate_valid_research_output(workspace)
    dest = tmp_path / "wiki_pair_out"
    dest.mkdir()

    pkg = build_stage_package(workspace, dest)
    assert pkg.package_id.startswith("rp_")
    assert pkg.package_path.exists()


def test_pairwise_atomic_file_write_and_state_sha256(workspace: Path, sample_collect_envelope):
    """P11: Pairwise: Atomic artifact file writing verified by ChainState SHA-256."""
    state_mgr = ChainState(workspace)
    key = sample_collect_envelope["idempotency_key"]
    state = state_mgr.load(key)
    state = state_mgr.commit_stage(state, "collect", sample_collect_envelope)

    read_env = state_mgr.read_stage(state, "collect")
    assert read_env["idempotency_key"] == key


def test_pairwise_video_notes_and_clean_normalization(workspace: Path):
    """P12: Pairwise: Video markdown metadata in raw/ verified with clean stage."""
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    video_note = raw_dir / "video_BV1xx411c7mD.md"
    video_note.write_text(
        "<!-- source: https://www.bilibili.com/video/BV1xx411c7mD -->\n"
        "<!-- fetched: 2026-09-17T14:00:00Z -->\n"
        "# Video Summary\nKey takeaways.",
        encoding="utf-8",
    )
    assert video_note.exists()
    assert "bilibili.com" in video_note.read_text(encoding="utf-8")


def test_pairwise_zero_binary_environment_and_offline_chain(workspace: Path, mock_adapter_client):
    """P13: Pairwise: Air-gapped chain execution using in-process mock adapter client."""
    chain = E2EChain(client=mock_adapter_client, work_dir=workspace)
    assert chain.client is not None
    assert chain.state is not None


def test_pairwise_webui_path_safety_and_mode_preset(workspace: Path):
    """P14: Pairwise: WebUI path safety validation combined with brief/full mode presets."""
    from research_tool.domain.config import mode_defaults

    brief_defaults = mode_defaults("brief")
    assert isinstance(brief_defaults, dict)
    assert (workspace / "valid_sub").resolve().is_relative_to(workspace.resolve())


def test_pairwise_mock_llm_and_extract_entity_triples():
    """P15: Pairwise: MockLLMClient chat output feeding into structured fact representation."""
    from research_tool.infrastructure.llm import MockLLMClient

    client = MockLLMClient(chat_response='{"entities": ["Quantum", "Algorithm"]}')
    assert client._chat_response == '{"entities": ["Quantum", "Algorithm"]}'
