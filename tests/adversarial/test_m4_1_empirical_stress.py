"""Adversarial stress test suite for Milestone 4 (Features F16–F18).

Authored by Challenger M4-1 (challenger_m4_1).

Objectives:
1. TargetedConfig boundary evasion (0, -1, -9999, 31, 1000, 'string', NaN, Inf) & synchronization.
2. TalkLinker CAS merge integrity & non-destructive preservation (clean/, extracted/, tree/, report.md never wiped).
3. 9-Stage DAG & CLI run modes invariants (--mode full, --mode brief, default, strict mode).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from research_tool.application.pipeline import PipelineResult, ResearchPipeline
from research_tool.application.talk_linker import (
    TalkEnrichReport,
    TalkLinker,
    TalkMatch,
)
from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import (
    PipelineConfig,
    TargetedConfig,
)
from research_tool.presentation.cli import app


# =========================================================================== #
# 1. TargetedConfig Boundaries & Sync Adversarial Tests
# =========================================================================== #


class TestTargetedConfigAdversarial:
    """Stress testing bounds, type evasion, and synchronization in TargetedConfig."""

    @pytest.mark.parametrize(
        "invalid_val",
        [0, -1, -5, -9999, -1000000, -(10**9)],
    )
    def test_max_queries_per_round_underflow_rejected(self, invalid_val: int):
        """Underflow boundary evasion: values <= 0 must raise ValidationError."""
        with pytest.raises(ValidationError) as excinfo:
            TargetedConfig(max_queries_per_round=invalid_val)
        err = str(excinfo.value)
        assert "greater than or equal to 1" in err

    @pytest.mark.parametrize(
        "invalid_val",
        [31, 32, 50, 100, 1000, 10**6, 10**9],
    )
    def test_max_queries_per_round_overflow_rejected(self, invalid_val: int):
        """Overflow boundary evasion: values > 30 must raise ValidationError."""
        with pytest.raises(ValidationError) as excinfo:
            TargetedConfig(max_queries_per_round=invalid_val)
        err = str(excinfo.value)
        assert "less than or equal to 30" in err

    @pytest.mark.parametrize(
        "invalid_type_val",
        [
            "string_payload",
            "NaN",
            "Infinity",
            [10],
            {"val": 10},
            float("nan"),
            float("inf"),
            float("-inf"),
        ],
    )
    def test_max_queries_per_round_type_and_nan_evasion_rejected(
        self, invalid_type_val: Any
    ):
        """Non-integer, NaN, and Inf inputs must be rejected by Pydantic."""
        with pytest.raises(ValidationError):
            TargetedConfig(max_queries_per_round=invalid_type_val)

    @pytest.mark.parametrize(
        "invalid_direct_max_queries",
        [0, -1, -9999, 31, 100, 1000],
    )
    def test_max_queries_direct_field_bounds(self, invalid_direct_max_queries: int):
        """Direct max_queries bounds must also enforce [1, 30]."""
        with pytest.raises(ValidationError):
            TargetedConfig(max_queries=invalid_direct_max_queries)

    @pytest.mark.parametrize(
        ("param_per_round", "expected"),
        [
            (1, 1),
            (2, 2),
            (10, 10),
            (15, 15),
            (29, 29),
            (30, 30),
        ],
    )
    def test_max_queries_per_round_valid_sync(
        self, param_per_round: int, expected: int
    ):
        """Valid max_queries_per_round synchronizes max_queries property bidirectionally."""
        tc = TargetedConfig(max_queries_per_round=param_per_round)
        assert tc.max_queries_per_round == expected
        assert tc.max_queries == expected

    @pytest.mark.parametrize("param_queries", [1, 5, 20, 30])
    def test_max_queries_fallback_sync(self, param_queries: int):
        """When max_queries_per_round is None, it inherits max_queries value."""
        tc = TargetedConfig(max_queries=param_queries)
        assert tc.max_queries == param_queries
        assert tc.max_queries_per_round == param_queries

    def test_precedence_when_both_specified(self):
        """When both are specified, max_queries_per_round takes explicit precedence."""
        tc = TargetedConfig(max_queries=10, max_queries_per_round=22)
        assert tc.max_queries_per_round == 22
        assert tc.max_queries == 22

    @pytest.mark.parametrize("invalid_val", [0, -10, 31, 500, "non_numeric"])
    def test_load_config_overrides_rejects_boundary_evasion(self, invalid_val: Any):
        """load_config must reject out-of-bounds targeted.max_queries_per_round."""
        with pytest.raises(ConfigValidationError) as excinfo:
            load_config(overrides={"targeted": {"max_queries_per_round": invalid_val}})
        err = str(excinfo.value)
        assert "targeted.max_queries_per_round" in err or "validation error" in err.lower()

    @pytest.mark.parametrize("invalid_val", [-1, 0, 31])
    def test_pipeline_config_rejects_invalid_targeted_dict(self, invalid_val: int):
        """PipelineConfig validation propagates TargetedConfig boundary errors."""
        with pytest.raises(ValidationError):
            PipelineConfig(targeted={"max_queries_per_round": invalid_val})

    def test_cli_max_targeted_queries_flag_bounds(self):
        """CLI --max-targeted-queries enforces [1, 30] bounds and permits valid input."""
        runner = CliRunner()

        # Underflow
        res_under = runner.invoke(app, ["run", "topic", "--max-targeted-queries", "0"])
        assert res_under.exit_code != 0
        assert "greater than or equal to 1" in res_under.output or "targeted.max_queries_per_round" in res_under.output

        # Overflow
        res_over = runner.invoke(app, ["run", "topic", "--max-targeted-queries", "31"])
        assert res_over.exit_code != 0
        assert "less than or equal to 30" in res_over.output or "targeted.max_queries_per_round" in res_over.output

        # Valid dry-run
        res_valid = runner.invoke(
            app, ["run", "topic", "--max-targeted-queries", "15", "--dry-run"]
        )
        assert res_valid.exit_code == 0
        assert "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report" in res_valid.output


# =========================================================================== #
# 2. TalkLinker CAS Merge Integrity & Non-Destructive Guarantees
# =========================================================================== #


class TestTalkLinkerCASIntegrity:
    """Stress testing TalkLinker CAS non-destructive merge guarantees."""

    @pytest.fixture
    def setup_populated_pipeline_dir(self, tmp_path):
        """Creates a fully populated pipeline directory with stage artifacts."""
        # Create directories
        clean_dir = tmp_path / "clean"
        clean_dir.mkdir(parents=True, exist_ok=True)
        (clean_dir / "doc_01.md").write_text("# Clean Doc 1\nContent", encoding="utf-8")

        extracted_dir = tmp_path / "extracted"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        (extracted_dir / "facts.json").write_text('{"facts": ["f1"]}', encoding="utf-8")

        tree_dir = tmp_path / "tree"
        tree_dir.mkdir(parents=True, exist_ok=True)
        (tree_dir / "00-主表.md").write_text("# Outline\nIndex", encoding="utf-8")
        (tree_dir / "node_01.md").write_text("# Node 1\nEvidence", encoding="utf-8")

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        talk_note = raw_dir / "talk_01.md"
        talk_note.write_text(
            "<!-- source: https://youtube.com/watch?v=mock123 -->\n"
            "<!-- title: Attention Is All You Need Talk -->\n"
            "<!-- from_paper: Attention Is All You Need -->\n"
            "<!-- talk_confidence: 0.95 -->\n\n"
            "# Talk Notes\nKey insights.",
            encoding="utf-8",
        )
        sources_file = raw_dir / "sources.json"
        sources_file.write_text(
            json.dumps(
                [
                    {
                        "title": "Attention Is All You Need",
                        "url": "https://arxiv.org/abs/1706.03762",
                        "source_engine": "arxiv",
                        "snippet": "2017 transformer paper",
                    }
                ]
            ),
            encoding="utf-8",
        )

        # Reports
        report_md = tmp_path / "report.md"
        report_md.write_text("# Existing Report\nVerified claims.", encoding="utf-8")
        report_html = tmp_path / "report.html"
        report_html.write_text("<html>Existing Report</html>", encoding="utf-8")

        # Initial state.json
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "topic": "test_topic",
                    "done": ["collect", "clean", "extract", "knowledge", "organize"],
                    "stages": {
                        "knowledge": {
                            "v": 1,
                            "run_id": "run_001",
                            "stage": "network",
                            "request_id": "req_001",
                            "idempotency_key": "initial_key",
                            "result": {
                                "nodes": [
                                    {
                                        "node_id": "N01",
                                        "kind": "paper",
                                        "evidence_spans": [
                                            {
                                                "locator": "arxiv:1706.03762",
                                                "content_sha256": "abc",
                                                "source_id": "src:01",
                                            }
                                        ],
                                    }
                                ],
                                "edges": [],
                                "counts": {"nodes": 1, "edges": 0},
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        return tmp_path

    @pytest.mark.asyncio
    async def test_talk_enrichment_never_deletes_clean_extracted_tree_or_report(
        self, setup_populated_pipeline_dir, monkeypatch
    ):
        """Enrichment must strictly preserve clean/, extracted/, tree/, and report.md."""
        tmp_path = setup_populated_pipeline_dir

        # Record file snapshots before enrichment
        clean_content_before = (tmp_path / "clean" / "doc_01.md").read_text(encoding="utf-8")
        extracted_content_before = (tmp_path / "extracted" / "facts.json").read_text(encoding="utf-8")
        tree_main_before = (tmp_path / "tree" / "00-主表.md").read_text(encoding="utf-8")
        report_md_before = (tmp_path / "report.md").read_text(encoding="utf-8")
        report_html_before = (tmp_path / "report.html").read_text(encoding="utf-8")

        cfg = PipelineConfig(
            topic="test_topic",
            work_dir=tmp_path,
            stages=["clean", "extract", "organize", "report"],
        )
        pipe = ResearchPipeline(cfg)

        async def mock_enrich(self, topic_dir, topic=""):
            return TalkEnrichReport(
                candidates=1,
                matched=[
                    TalkMatch(
                        paper_title="Attention Is All You Need",
                        video_url="https://youtube.com/watch?v=mock123",
                        confidence=0.95,
                        matched=True,
                        video_title="Attention Is All You Need Talk",
                    )
                ],
                files_written=[tmp_path / "raw" / "talk_01.md"],
            )

        monkeypatch.setattr(
            "research_tool.application.talk_linker.TalkLinker.enrich", mock_enrich
        )

        executed_legacy_stages: list[str] = []

        async def spy_exec(stage, topic, topic_dir, result):
            executed_legacy_stages.append(stage)

        monkeypatch.setattr(pipe, "_exec", spy_exec)

        result_obj = PipelineResult(topic_dir=tmp_path)
        events = [
            event
            async for event in pipe._run_talk_enrichment(
                "test_topic", tmp_path, result_obj
            )
        ]

        # Verify NO legacy stage re-execution was invoked
        assert executed_legacy_stages == [], "Legacy _exec was called during talk enrichment!"

        # Verify ALL directory artifacts preserved intact
        assert (tmp_path / "clean" / "doc_01.md").read_text(encoding="utf-8") == clean_content_before
        assert (tmp_path / "extracted" / "facts.json").read_text(encoding="utf-8") == extracted_content_before
        assert (tmp_path / "tree" / "00-主表.md").read_text(encoding="utf-8") == tree_main_before
        assert (tmp_path / "report.md").read_text(encoding="utf-8") == report_md_before
        assert (tmp_path / "report.html").read_text(encoding="utf-8") == report_html_before

        # Verify merge stage completed via CAS
        assert any(ev.stage == "merge" and ev.status == "completed" for ev in events)
        assert "merge" in result_obj.stages_completed

        # Verify state.json recorded merge
        state_after = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert "merge" in state_after.get("stages", {})

    @pytest.mark.asyncio
    async def test_repeated_and_force_enrichment_idempotence(
        self, setup_populated_pipeline_dir, monkeypatch
    ):
        """Repeated calls to talk enrichment must be idempotent and non-destructive."""
        tmp_path = setup_populated_pipeline_dir
        cfg = PipelineConfig(
            topic="test_topic",
            work_dir=tmp_path,
            stages=["clean", "extract", "organize", "report"],
        )
        pipe = ResearchPipeline(cfg)

        call_count = 0

        async def mock_enrich(self, topic_dir, topic=""):
            nonlocal call_count
            call_count += 1
            return TalkEnrichReport(
                candidates=1,
                matched=[
                    TalkMatch(
                        paper_title="Attention Is All You Need",
                        video_url="https://youtube.com/watch?v=mock123",
                        confidence=0.95,
                        matched=True,
                    )
                ],
                files_written=[tmp_path / "raw" / "talk_01.md"],
            )

        monkeypatch.setattr(
            "research_tool.application.talk_linker.TalkLinker.enrich", mock_enrich
        )

        # Call 1
        res1 = PipelineResult(topic_dir=tmp_path)
        events1 = [ev async for ev in pipe._run_talk_enrichment("test_topic", tmp_path, res1)]
        assert any(ev.stage == "merge" for ev in events1)

        # Call 2 (Subsequent run on same dir)
        res2 = PipelineResult(topic_dir=tmp_path)
        events2 = [ev async for ev in pipe._run_talk_enrichment("test_topic", tmp_path, res2)]
        assert any(ev.stage == "merge" for ev in events2)

        # Verify files remain strictly intact
        assert (tmp_path / "clean" / "doc_01.md").exists()
        assert (tmp_path / "tree" / "00-主表.md").exists()
        assert (tmp_path / "report.md").exists()

    def test_to_merge_responses_adversarial_inputs(self):
        """Verify to_merge_responses handles empty, malformed, and unicode inputs cleanly."""
        linker = TalkLinker()

        # Empty inputs
        assert linker.to_merge_responses([]) == []
        assert linker.to_merge_responses(TalkEnrichReport()) == []
        assert linker.to_merge_responses(None) == []  # type: ignore

        # Filter un-matched or missing URL
        unmatched = [
            TalkMatch(paper_title="P1", video_url=None, confidence=0.0, matched=False),
            TalkMatch(paper_title="P2", video_url="", confidence=0.8, matched=True),
            TalkMatch(paper_title="P3", video_url="https://yt.com/1", confidence=0.4, matched=False),
        ]
        assert linker.to_merge_responses(unmatched) == []

        # Complex unicode, special symbols, long strings
        adversarial_matches = [
            TalkMatch(
                paper_title="量子纠缠与深度学习：面向超大规模图卷积网络的理论与实践 🚀!@#$%^&*()_+",
                video_url="https://youtube.com/watch?v=unicode_test_99",
                confidence=0.987654321,
                matched=True,
                video_title="清华大学学术报告会 2026 📹 — 深度剖析",
            ),
            TalkMatch(
                paper_title="A" * 500,
                video_url="https://youtube.com/watch?v=long_title_test",
                confidence=1.0,
                matched=True,
                video_title="B" * 500,
            ),
        ]
        responses = linker.to_merge_responses(adversarial_matches)
        assert len(responses) == 2
        for r in responses:
            assert "request_id" in r
            assert len(r["facts"]) == 1
            fact = r["facts"][0]
            assert len(fact["content_sha256"]) == 64
            assert fact["extractor_version"] == "talk_linker.v1"
            assert fact["round_id"] == "talk_enrichment"


# =========================================================================== #
# 3. 9-Stage DAG & Run Modes Invariants
# =========================================================================== #


class TestRunModesInvariants:
    """Stress testing DAG stage sequences, CLI run modes, and strict interception."""

    def test_default_mode_dry_run_executes_canonical_nine_stages(self):
        """Default CLI invocation runs full 9 stages in exact canonical order."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "topic_adversarial", "--dry-run"])

        assert result.exit_code == 0
        assert "mode=full" in result.output
        expected_dag = (
            "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report"
        )
        assert expected_dag in result.output
        # Ensure deprecated stage names are purged
        assert "deepen" not in result.output
        assert "organize" not in result.output

    def test_full_mode_dry_run_executes_canonical_nine_stages(self):
        """Explicit --mode full executes the full 9 stages."""
        runner = CliRunner()
        result = runner.invoke(
            app, ["run", "topic_adversarial", "--mode", "full", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "mode=full" in result.output
        expected_dag = (
            "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report"
        )
        assert expected_dag in result.output

    @pytest.mark.parametrize(
        "stage_to_skip",
        ["clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate"],
    )
    def test_full_mode_rejects_skip_flags(self, stage_to_skip: str):
        """--mode full strictly forbids --skip flags to maintain closed-loop completeness."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["run", "topic_adversarial", "--mode", "full", "--skip", stage_to_skip, "--dry-run"],
        )
        assert result.exit_code != 0
        assert "--mode full 强制执行完整九段闭环管线，不能同时使用 --skip" in result.output

    def test_brief_mode_non_strict_warning_and_stages(self, monkeypatch):
        """Under non-strict environment, --mode brief emits warning and limits to 3 stages."""
        monkeypatch.delenv("RESEARCH_AGENT_STRICT", raising=False)
        runner = CliRunner()
        result = runner.invoke(
            app, ["run", "topic_adversarial", "--mode", "brief", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "mode=brief" in result.output
        assert "collect → clean → report" in result.output
        assert "警告" in result.output or "WARNING" in result.output
        assert "跳过抽取、知识网络与闭环自愈" in result.output

    def test_brief_mode_blocked_in_strict_agent_mode(self, monkeypatch):
        """Under RESEARCH_AGENT_STRICT=1, --mode brief must be hard-blocked."""
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")
        runner = CliRunner()
        result = runner.invoke(
            app, ["run", "topic_adversarial", "--mode", "brief", "--dry-run"]
        )

        assert result.exit_code != 0
        assert "安全拦截（RESEARCH_AGENT_STRICT）" in result.output
        assert "严禁使用 --mode brief 偷懒缩水" in result.output

    def test_invalid_mode_rejected_with_actionable_error(self):
        """Unrecognized modes must fail with an informative error message."""
        runner = CliRunner()
        result = runner.invoke(
            app, ["run", "topic_adversarial", "--mode", "ultra_fast_bogus", "--dry-run"]
        )
        assert result.exit_code != 0
        assert "未知调研模式: ultra_fast_bogus" in result.output
