"""CLI mode selection and real configuration validation, without research I/O."""

import pytest
from typer.testing import CliRunner

from research_tool.domain.errors import ConfigValidationError
from research_tool.presentation.cli import app


EXPECTED_CHAIN = (
    "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report"
)


@pytest.fixture(autouse=True)
def _isolate_local_config(monkeypatch):
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)
    monkeypatch.setattr("research_tool.domain.config._DEFAULT_PATHS", [])


@pytest.mark.parametrize("mode", ["full", "standard", "deep"])
def test_retained_modes_dry_run_lists_complete_chain(mode):
    result = CliRunner().invoke(app, ["run", "topic", "--mode", mode, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"mode={mode}" in result.output
    assert EXPECTED_CHAIN in result.output


def test_dry_run_uses_mode_from_config_when_cli_mode_is_omitted(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("pipeline:\n  mode: deep\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "mode=deep" in result.output
    assert EXPECTED_CHAIN in result.output


def test_cli_mode_overrides_config_without_changing_chain(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("pipeline:\n  mode: standard\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--mode", "deep", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "mode=deep" in result.output
    assert EXPECTED_CHAIN in result.output


@pytest.mark.parametrize("mode", ["brief", "fast"])
def test_dry_run_rejects_removed_config_modes(tmp_path, mode):
    config = tmp_path / "config.yaml"
    config.write_text(f"pipeline:\n  mode: {mode}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--dry-run"],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigValidationError)
    assert mode in str(result.exception)
    assert "将执行的阶段" not in result.output


@pytest.mark.parametrize(
    "stages",
    [
        "[collect, clean, report]",
        "[collect, extract, clean, knowledge, inspect, targeted, merge, qgate, report]",
    ],
    ids=["partial", "reordered"],
)
def test_dry_run_rejects_partial_or_reordered_config_stages(tmp_path, stages):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"pipeline:\n  mode: standard\n  stages: {stages}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--dry-run"],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigValidationError)
    assert "九段" in str(result.exception)
    assert "将执行的阶段" not in result.output
