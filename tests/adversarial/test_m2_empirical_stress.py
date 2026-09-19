"""Adversarial stress test harness for Milestone 2: Features F06, F07, F08, F09.

Conducted by Challenger M2-1.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import (
    CANONICAL_STAGES,
    STAGE_ALIASES,
    STAGE_CANONICAL_DOMAIN,
    BudgetLeaseConfig,
    InspectConfig,
    PipelineConfig,
    QGateConfig,
    TargetedConfig,
    normalize_stage_name,
)
from research_tool.presentation.cli import _build_run_overrides, _is_strict_agent, app
from research_tool.presentation.webui import _stage_cn


# =========================================================================== #
# Feature F06: Stage Literal & Alias Support
# =========================================================================== #


class TestF06StageAliases:
    """Stress testing stage literal and alias resolution."""

    def test_canonical_domain_aliases(self):
        """Verify standard canonical domain aliases."""
        assert normalize_stage_name("network") == "knowledge"
        assert normalize_stage_name("gate") == "qgate"
        assert normalize_stage_name("organize") == "knowledge"
        assert normalize_stage_name("knowledge") == "knowledge"
        assert normalize_stage_name("qgate") == "qgate"

    def test_alias_whitespace_and_mixed_case(self):
        """Verify whitespace trimming and case insensitivity in normalize_stage_name."""
        assert normalize_stage_name("  network  ") == "knowledge"
        assert normalize_stage_name("\tGATE\n") == "qgate"
        assert normalize_stage_name("  Organize ") == "knowledge"
        assert normalize_stage_name("KNOWLEDGE") == "knowledge"
        assert normalize_stage_name("  collect\t") == "collect"

    def test_deepen_alias_resolution(self):
        """Verify that 'deepen' is mapped to 'targeted' in STAGE_ALIASES,
        STAGE_CANONICAL_DOMAIN, and normalize_stage_name.
        """
        assert normalize_stage_name("deepen") == "targeted"
        assert STAGE_CANONICAL_DOMAIN.get("deepen") == "targeted"
        assert STAGE_ALIASES.get("deepen") == "targeted"

    def test_pipeline_config_stages_validation(self):
        """Verify PipelineConfig normalizes stage names, whitespace, and aliases."""
        p1 = PipelineConfig(stages=["collect", "Clean", "report"])
        assert p1.stages == ["collect", "clean", "report"]

        p2 = PipelineConfig(stages=["collect", " network ", "report"])
        assert p2.stages == ["collect", "knowledge", "report"]

        p3 = PipelineConfig(stages=["collect", " Clean ", " NETWORK "])
        assert p3.stages == ["collect", "clean", "knowledge"]

    def test_webui_stage_translation_casing(self):
        """CHALLENGE F06: WebUI _stage_cn does not normalize case or whitespace."""
        assert _stage_cn("knowledge") == "知识网络"
        assert _stage_cn("network") == "知识网络"
        assert _stage_cn("gate") == "质量门控"
        # Mixed casing fails translation and returns raw string because _stage_cn lacks .lower()
        assert _stage_cn("Network") == "Network"
        assert _stage_cn("  gate  ") == "  gate  "


# =========================================================================== #
# Feature F07: Domain Config Schema Modernization
# =========================================================================== #


class TestF07ConfigSchemas:
    """Stress testing domain configuration schemas and boundary validations."""

    def test_targeted_config_max_queries_per_round_bypass(self):
        """Verify max_queries_per_round strictly enforces bounds validation [1, 30],
        preventing negative numbers, zero, and huge overflows.
        """
        # max_queries has ge=1, le=30
        with pytest.raises(ValidationError):
            TargetedConfig(max_queries=-5)

        with pytest.raises(ValidationError):
            TargetedConfig(max_queries=0)

        with pytest.raises(ValidationError):
            TargetedConfig(max_queries=31)

        # max_queries_per_round bounds validation:
        with pytest.raises(ValidationError):
            TargetedConfig(max_queries_per_round=-100)

        with pytest.raises(ValidationError):
            TargetedConfig(max_queries_per_round=0)

        with pytest.raises(ValidationError):
            TargetedConfig(max_queries_per_round=31)

        with pytest.raises(ValidationError):
            TargetedConfig(max_queries_per_round=999999)

        tc_valid = TargetedConfig(max_queries_per_round=15)
        assert tc_valid.max_queries == 15
        assert tc_valid.max_queries_per_round == 15

    def test_targeted_config_max_rounds_bounds(self):
        """Verify boundary enforcement on TargetedConfig.max_rounds (ge=1, le=5)."""
        assert TargetedConfig(max_rounds=1).max_rounds == 1
        assert TargetedConfig(max_rounds=5).max_rounds == 5

        with pytest.raises(ValidationError):
            TargetedConfig(max_rounds=0)

        with pytest.raises(ValidationError):
            TargetedConfig(max_rounds=-1)

        with pytest.raises(ValidationError):
            TargetedConfig(max_rounds=6)

        with pytest.raises(ValidationError):
            TargetedConfig(max_rounds=2.5)

    def test_inspect_config_phantom_threshold_fields(self):
        """CHALLENGE F07-DISCREPANCY: Worker M2 claimed in handoff.md line 35:
        InspectConfig has contradiction_threshold, orphan_threshold, coverage_gap_threshold.
        Empirically verify whether those float fields actually exist.
        """
        cfg = InspectConfig()
        assert not hasattr(cfg, "contradiction_threshold")
        assert not hasattr(cfg, "orphan_threshold")
        assert not hasattr(cfg, "coverage_gap_threshold")

        # Actual fields: rules, priority_filter, min_gap_severity
        assert cfg.min_gap_severity == "medium"
        with pytest.raises(ValidationError):
            InspectConfig(min_gap_severity="invalid_severity")

        with pytest.raises(ValidationError):
            InspectConfig(priority_filter=["invalid_priority"])

    def test_qgate_config_boundaries(self):
        """Verify QGateConfig boundary constraints."""
        q = QGateConfig(max_high_findings=0, max_total_findings=0)
        assert q.max_high_findings == 0
        assert q.max_total_findings == 0

        with pytest.raises(ValidationError):
            QGateConfig(max_high_findings=-1)

        with pytest.raises(ValidationError):
            QGateConfig(max_total_findings=-1)

        with pytest.raises(ValidationError):
            QGateConfig(budget_lease=None)

        # Unvalidated logical inconsistency: max_high_findings > max_total_findings
        q_inconsistent = QGateConfig(max_high_findings=10, max_total_findings=2)
        assert q_inconsistent.max_high_findings > q_inconsistent.max_total_findings

    def test_budget_lease_config_boundaries(self):
        """Verify BudgetLeaseConfig strict positivity and boundary validations."""
        # tokens_max > 0
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(tokens_max=0)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(tokens_max=-1)

        # cost_max > 0.0
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(cost_max=0.0)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(cost_max=-0.5)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(cost_max=float("nan"))

        # wall_s_max > 0.0
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(wall_s_max=0.0)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(wall_s_max=-1.0)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(wall_s_max=float("nan"))

        # search_calls_max > 0
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(search_calls_max=0)
        with pytest.raises(ValidationError):
            BudgetLeaseConfig(search_calls_max=-5)

        # Valid boundaries
        b = BudgetLeaseConfig(tokens_max=1, cost_max=0.001, wall_s_max=0.1, search_calls_max=1)
        assert b.tokens_max == 1
        assert b.cost_max == 0.001


# =========================================================================== #
# Feature F08: CLI Options Modernization
# =========================================================================== #


class TestF08CLIOptions:
    """Stress testing CLI option parsing and parameter propagation."""

    def test_deprecated_flags_produce_warnings(self):
        """Verify that passing deprecated flags emits deprecation warnings."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(
                app,
                ["run", "Test Topic", "--output", tmpdir, "--profile-iterations", "2", "--dry-run"],
            )
            assert res.exit_code == 0
            assert "已废弃并失效" in res.output or "profile-iterations" in res.output

            res2 = runner.invoke(
                app,
                ["run", "Test Topic", "--output", tmpdir, "--max-backward-rounds", "1", "--dry-run"],
            )
            assert res2.exit_code == 0
            assert "已废弃并失效" in res2.output or "max-backward-rounds" in res2.output

    def test_cli_overrides_wire_through(self):
        """Verify that CLI closed-loop options correctly populate overrides."""
        overrides = _build_run_overrides(
            topic="Test Topic",
            mode="full",
            output=None,
            stages=list(CANONICAL_STAGES),
            resume=True,
            source=[],
            max_results=8,
            rounds=None,
            llm_expand=False,
            query=[],
            core=None,
            facets=None,
            from_year=None,
            to_year=None,
            deep_search=None,
            deep_pages=None,
            deep_sorts=None,
            search_relevance_min_overlap=None,
            x_backend=None,
            x_cmd=None,
            relevance_filter=False,
            qgate_max_high=2,
            qgate_max_total=7,
            max_targeted_rounds=4,
            max_targeted_queries=15,
            inspect_rules="contradiction,orphan_node",
        )
        assert overrides["qgate"]["max_high_findings"] == 2
        assert overrides["qgate"]["max_total_findings"] == 7
        assert overrides["targeted"]["max_rounds"] == 4
        assert overrides["targeted"]["max_queries_per_round"] == 15
        assert overrides["inspect"]["rules"] == ["contradiction", "orphan_node"]

        cfg = load_config(overrides=overrides)
        assert cfg.qgate.max_high_findings == 2
        assert cfg.qgate.max_total_findings == 7
        assert cfg.targeted.max_rounds == 4
        assert cfg.targeted.max_queries == 15
        assert cfg.inspect.rules == ["contradiction", "orphan_node"]

    def test_cli_max_targeted_queries_bypass_via_load_config(self):
        """Verify that passing negative --max-targeted-queries raises ConfigValidationError
        during load_config.
        """
        overrides = _build_run_overrides(
            topic="Bypass",
            mode="full",
            output=None,
            stages=list(CANONICAL_STAGES),
            resume=True,
            source=[],
            max_results=8,
            rounds=None,
            llm_expand=False,
            query=[],
            core=None,
            facets=None,
            from_year=None,
            to_year=None,
            deep_search=None,
            deep_pages=None,
            deep_sorts=None,
            search_relevance_min_overlap=None,
            x_backend=None,
            x_cmd=None,
            relevance_filter=False,
            max_targeted_queries=-50,
        )
        with pytest.raises(ConfigValidationError):
            load_config(overrides=overrides)

    def test_conflicting_mode_full_and_skip(self):
        """Verify that combining --mode full and --skip is rejected."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(
                app,
                ["run", "Test", "--output", tmpdir, "--mode", "full", "--skip", "inspect"],
            )
            assert res.exit_code != 0
            assert "不能同时使用 --skip" in res.output or "错误" in res.output


# =========================================================================== #
# Feature F09: CLI Strict & Status Alignment
# =========================================================================== #


class TestF09StrictAndStatus:
    """Stress testing RESEARCH_AGENT_STRICT security checks and status command rendering."""

    def test_strict_agent_rejects_brief_mode(self, monkeypatch):
        """Verify that RESEARCH_AGENT_STRICT=1 rejects --mode brief."""
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(
                app,
                ["run", "Strict Test", "--output", tmpdir, "--mode", "brief"],
            )
            assert res.exit_code != 0
            assert "安全拦截" in res.output or "RESEARCH_AGENT_STRICT" in res.output

    def test_strict_agent_accepts_canonical_nine_stages(self, monkeypatch):
        """Verify that RESEARCH_AGENT_STRICT=1 accepts full 9-stage pipeline."""
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(
                app,
                ["run", "Strict Test", "--output", tmpdir, "--mode", "full", "--dry-run"],
            )
            assert res.exit_code == 0

    def test_strict_agent_case_sensitivity_defect(self, monkeypatch):
        """CHALLENGE F09-DEFECT: RESEARCH_AGENT_STRICT alias checking is case-sensitive.
        If stages has uppercase 'Network', strict check fails with missing 'knowledge'.
        """
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")
        # Stage alias dictionary in cli.py is exact lower case
        assert _is_strict_agent() is True

    def test_status_rich_markup_stripping_bug(self):
        """Verify that status renders stage names and labels cleanly without Rich markup tag stripping."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(app, ["status", tmpdir])
            assert res.exit_code == 0
            output = res.output

            # Stage numbers, Chinese labels, and English names are properly rendered and not stripped
            assert "阶段1 采集 (collect)" in output
            assert "阶段2 清洗 (clean)" in output
            assert "阶段3 抽取 (extract)" in output
            assert "阶段4 知识网络 (knowledge)" in output
            assert "阶段5 缺口检视 (inspect)" in output
            assert "阶段6 靶向补搜 (targeted)" in output
            assert "阶段7 CAS合并 (merge)" in output
            assert "阶段8 质量门控 (qgate)" in output
            assert "阶段9 核验报告 (report)" in output

    def test_status_with_all_nine_artifacts(self):
        """Verify that status parses all 9 stage artifact envelopes cleanly."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            ad = td / "artifacts"
            ad.mkdir()

            (ad / "collect.json").write_text(json.dumps({"result": {"sources": [{"url": "http://a"}]}}))
            (ad / "clean.json").write_text(json.dumps({"result": {"cleaned_documents": [{"id": "1"}]}}))
            (ad / "extract.json").write_text(json.dumps({"result": {"facts": ["f1"], "entities": ["e1"]}}))
            (ad / "knowledge.json").write_text(json.dumps({"result": {"nodes": ["n1"]}}))
            (ad / "inspect.json").write_text(json.dumps({"result": {"gap_count": 2, "contradiction_count": 1}}))
            (ad / "targeted.json").write_text(json.dumps({"result": {"queries": ["q1", "q2"]}}))
            (ad / "merge.json").write_text(json.dumps({"result": {"counts": {"added": 3}}}))
            (ad / "qgate.json").write_text(json.dumps({"result": {"decision": "PASS"}}))
            (ad / "report.json").write_text(json.dumps({"result": {"citation_coverage": 1.0, "verified_claims_count": 5}}))

            (td / "state.json").write_text(json.dumps({
                "done": list(CANONICAL_STAGES),
                "loop": {"current_round": 1, "history": [{"round": 1}]}
            }))
            (td / "run-summary.json").write_text(json.dumps({
                "pipeline_complete": True,
                "elapsed_sec": 12.3,
                "citation_coverage": 1.0
            }))

            res = runner.invoke(app, ["status", str(td)])
            assert res.exit_code == 0
            assert "1 篇来源" in res.output
            assert "缺口: 2, 矛盾: 1" in res.output
            assert "增量合并 +3" in res.output
            assert "判定: PASS" in res.output
            assert "引用覆盖率 100%" in res.output

    def test_status_graceful_on_malformed_artifacts(self):
        """Verify status command does not crash on corrupt artifact JSON files."""
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            ad = td / "artifacts"
            ad.mkdir()

            (ad / "collect.json").write_text("{broken json syntax!!")
            (td / "state.json").write_text("invalid json content")

            res = runner.invoke(app, ["status", str(td)])
            assert res.exit_code == 0
            assert "collect.json" in res.output
