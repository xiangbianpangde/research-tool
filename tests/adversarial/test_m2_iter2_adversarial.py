"""Adversarial stress test harness for Milestone 2 Iteration 2 remediation.

Authored by Challenger M2 Iteration 2 (1).
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
    STAGE_ALIASES,
    STAGE_CANONICAL_DOMAIN,
    PipelineConfig,
    TargetedConfig,
    normalize_stage_name,
)
from research_tool.presentation.cli import app
from research_tool.presentation.webui import run_web


class TestTargetedConfigAdversarial:
    """Adversarial testing of TargetedConfig bounds validation and synchronization."""

    @pytest.mark.parametrize("invalid_val", [-1000, -100, -50, -1, 0, 31, 32, 100, 1000, 10**9])
    def test_max_queries_per_round_out_of_bounds_rejected(self, invalid_val: int):
        with pytest.raises(ValidationError) as excinfo:
            TargetedConfig(max_queries_per_round=invalid_val)
        err = str(excinfo.value)
        if invalid_val < 1:
            assert "greater than or equal to 1" in err
        else:
            assert "less than or equal to 30" in err

    @pytest.mark.parametrize("valid_val", [1, 2, 10, 15, 29, 30])
    def test_max_queries_per_round_valid_bounds_accepted(self, valid_val: int):
        tc = TargetedConfig(max_queries_per_round=valid_val)
        assert tc.max_queries_per_round == valid_val
        assert tc.max_queries == valid_val

    @pytest.mark.parametrize("invalid_val", [-50, -1, 0, 31, 100])
    def test_load_config_overrides_rejected(self, invalid_val: int):
        with pytest.raises(ConfigValidationError) as excinfo:
            load_config(overrides={"targeted": {"max_queries_per_round": invalid_val}})
        err = str(excinfo.value)
        assert "targeted.max_queries_per_round" in err

    def test_cli_max_targeted_queries_negative_flag_rejected(self):
        runner = CliRunner()
        res = runner.invoke(app, ["run", "topic", "--max-targeted-queries", "-50"])
        assert res.exit_code != 0
        assert "targeted.max_queries_per_round" in res.output or "greater than or equal to 1" in res.output

    def test_cli_max_targeted_queries_overflow_flag_rejected(self):
        runner = CliRunner()
        res = runner.invoke(app, ["run", "topic", "--max-targeted-queries", "100"])
        assert res.exit_code != 0
        assert "targeted.max_queries_per_round" in res.output or "less than or equal to 30" in res.output


class TestStatusRenderingAdversarial:
    """Adversarial testing of CLI status command table rendering and markup escaping."""

    def test_status_renders_both_chinese_label_and_canonical_name(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            res = runner.invoke(app, ["status", tmpdir])
            assert res.exit_code == 0
            output = res.output

            expected_stages = [
                "阶段1 采集 (collect)",
                "阶段2 清洗 (clean)",
                "阶段3 抽取 (extract)",
                "阶段4 知识网络 (knowledge)",
                "阶段5 缺口检视 (inspect)",
                "阶段6 靶向补搜 (targeted)",
                "阶段7 CAS合并 (merge)",
                "阶段8 质量门控 (qgate)",
                "阶段9 核验报告 (report)",
            ]
            for s in expected_stages:
                assert s in output, f"Missing stage row: {s}"

    def test_status_null_keys_in_state_json_does_not_crash(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(Path(tmpdir) / "state.json", "w", encoding="utf-8") as f:
                json.dump({"done": None, "loop": None}, f)
            res = runner.invoke(app, ["status", tmpdir])
            assert res.exit_code == 0
            assert "阶段1 采集 (collect)" in res.output
            assert "阶段6 靶向补搜 (targeted)" in res.output


class TestStageNormalizationAdversarial:
    """Adversarial testing of stage alias and whitespace normalization."""

    def test_pipeline_config_whitespace_and_mixed_case(self):
        cfg = PipelineConfig(stages=["collect", " Clean ", " NETWORK "])
        assert cfg.stages == ["collect", "clean", "knowledge"]

    def test_pipeline_config_canonical_aliases(self):
        cfg = PipelineConfig(stages=[" COLLECT ", "clean", "GATE", " targeted "])
        assert cfg.stages == ["collect", "clean", "qgate", "targeted"]

    def test_deepen_in_closed_loop_maps_to_targeted(self):
        cfg = PipelineConfig(stages=["collect", "clean", "extract", "knowledge", "inspect", "deepen", "merge", "qgate", "report"])
        assert cfg.stages == ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]

    def test_deepen_and_organize_in_legacy_preserved(self):
        cfg = PipelineConfig(stages=["collect", "deepen", "clean", "extract", "organize", "report"])
        assert cfg.stages == ["collect", "deepen", "clean", "extract", "organize", "report"]

    def test_stage_alias_dictionaries_contain_deepen(self):
        assert STAGE_ALIASES.get("deepen") == "targeted"
        assert STAGE_CANONICAL_DOMAIN.get("deepen") == "targeted"
        assert normalize_stage_name("deepen") == "targeted"


class TestWebUIStrictTruthinessAdversarial:
    """Adversarial testing of WebUI strict mode truthiness parsing."""

    @pytest.mark.parametrize("val", ["1", "true", "yes", "True", "YES", "  true  "])
    def test_webui_strict_truthy(self, val: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", val)
        gen = run_web(
            topic="Strict WebUI Test", mode="brief", source_types=["web"], engines=["web"],
            max_results=5, rounds=1, llm_expand=False, query_text="", core_topic="",
            facets="", from_year="", to_year="", deep_search=False, deep_pages=1,
            deep_sorts=["relevance"], min_overlap=0.1, relevance_filter=True, style="academic",
            min_nodes=3, max_nodes=5, do_extract=True, pdf_path="", mineru_cmd="mineru",
            translate=False, provider="deepseek", model="deepseek-chat", base_url="",
            api_key="", work_dir="./research-output", qgate_max_high=0, qgate_max_total=10,
            max_targeted_rounds=3, max_targeted_queries=10,
        )
        out = next(gen)[0]
        assert "安全拦截：当前环境开启了 RESEARCH_AGENT_STRICT" in out
