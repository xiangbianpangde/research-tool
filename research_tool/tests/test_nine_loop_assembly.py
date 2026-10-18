"""Public configuration assembly for the single nine-stage research loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline, create_pipeline
from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import NineLoopConfig, PipelineConfig


EXPECTED_LIMITS = {
    "max_rounds": 2,
    "max_high_findings": 0,
    "max_total_findings": 0,
    "wall_s_max": 1800,
}


@pytest.fixture(autouse=True)
def _isolate_local_config(monkeypatch):
    for name in (
        "RESEARCH_CONFIG", "RESEARCH_WORK_DIR", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL",
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MINIMAX_API_KEY",
        "TAVILY_API_KEY", "GITHUB_TOKEN", "S2_API_KEY", "OPENALEX_MAILTO", "HTTPS_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("research_tool.domain.config._DEFAULT_PATHS", [])


def test_config_schema_defaults_to_bounded_nine_loop(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("topic: t\npipeline:\n  work_dir: ./out\n", encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.nine_loop.model_dump() == EXPECTED_LIMITS


@pytest.mark.parametrize("nested", [False, True], ids=["top-level", "pipeline"])
def test_config_schema_parses_nine_loop_limits(tmp_path: Path, nested: bool) -> None:
    cfg_path = tmp_path / "config.yaml"
    limits = (
        "nine_loop:\n  max_rounds: 4\n  max_high_findings: 1\n"
        "  max_total_findings: 3\n  wall_s_max: 60\n"
    )
    if nested:
        limits = "".join("  " + line for line in limits.splitlines(keepends=True))
    cfg_path.write_text("topic: t\npipeline:\n  work_dir: ./out\n" + limits, encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.nine_loop.model_dump() == {
        "max_rounds": 4,
        "max_high_findings": 1,
        "max_total_findings": 3,
        "wall_s_max": 60,
    }


def test_nine_loop_overrides_merge_without_dropping_other_limits(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "nine_loop:\n  max_rounds: 4\n  max_high_findings: 1\n"
        "  max_total_findings: 3\n  wall_s_max: 60\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path, overrides={"nine_loop": {"max_rounds": 1, "wall_s_max": 30}})

    assert cfg.nine_loop.model_dump() == {
        "max_rounds": 1,
        "max_high_findings": 1,
        "max_total_findings": 3,
        "wall_s_max": 30,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_rounds", -1), ("max_rounds", 11), ("max_high_findings", -1),
        ("max_total_findings", -1), ("wall_s_max", 0),
    ],
)
def test_invalid_nine_loop_bounds_are_config_errors(tmp_path: Path, field: str, value) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(f"nine_loop:\n  {field}: {value}\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=field):
        load_config(cfg_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", "false"),
        ("gate_enabled", "false"),
        ("shadow_enabled", "true"),
        ("shadow_sample_rate", "0.5"),
        ("stages", "{collect: true}"),
    ],
)
def test_legacy_nine_loop_flags_are_config_errors(tmp_path: Path, field: str, value: str) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(f"nine_loop:\n  {field}: {value}\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=rf"nine_loop\.{field}"):
        load_config(cfg_path)


def test_create_pipeline_preserves_nine_loop_limits(tmp_path: Path) -> None:
    cfg = PipelineConfig(
        topic="t", work_dir=tmp_path / "output",
        nine_loop=NineLoopConfig(max_rounds=4, max_high_findings=1, max_total_findings=3, wall_s_max=60),
    )

    pipe = create_pipeline(cfg)

    assert isinstance(pipe, ResearchPipeline)
    assert pipe.config is cfg
    assert pipe.config.nine_loop.model_dump() == {
        "max_rounds": 4,
        "max_high_findings": 1,
        "max_total_findings": 3,
        "wall_s_max": 60,
    }


def test_formal_pipeline_rejects_search_snippets_as_source_bodies():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="depth"):
        PipelineConfig(collector={"depth": 1})


def test_formal_pipeline_rejects_silently_ignored_html_output():
    with pytest.raises(ValueError, match="markdown"):
        PipelineConfig(reporter={"format": "html"})


def test_formal_pipeline_rejects_ambiguous_input_selection():
    with pytest.raises(ValueError, match="input_dir.*pdf_dir"):
        PipelineConfig(input_dir=Path("raw"), pdf_dir="papers")
