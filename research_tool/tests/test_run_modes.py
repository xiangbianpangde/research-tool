from typer.testing import CliRunner

from research_tool.presentation.cli import app


def test_default_dry_run_executes_native_nine_stages():
    """Default CLI execution without flags runs full 9-stage pipeline."""
    result = CliRunner().invoke(app, ["run", "topic", "--dry-run"])

    assert result.exit_code == 0
    assert "mode=full" in result.output
    assert "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report" in result.output


def test_full_mode_dry_run_executes_native_nine_stages():
    """Explicit --mode full runs full 9-stage pipeline."""
    result = CliRunner().invoke(app, ["run", "topic", "--mode", "full", "--dry-run"])

    assert result.exit_code == 0
    assert "mode=full" in result.output
    assert "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report" in result.output


def test_brief_mode_dry_run_executes_three_stages_with_warning():
    """--mode brief runs collect -> clean -> report and emits a warning."""
    result = CliRunner().invoke(app, ["run", "topic", "--mode", "brief", "--dry-run"])

    assert result.exit_code == 0
    assert "mode=brief" in result.output
    assert "collect → clean → report" in result.output
    assert "警告" in result.output or "WARNING" in result.output


def test_fast_mode_dry_run_skips_deepen_without_network():
    result = CliRunner().invoke(app, ["run", "topic", "--mode", "fast", "--dry-run"])

    assert result.exit_code == 0
    assert "collect" in result.output
    assert "deepen" not in result.output


def test_deep_mode_dry_run_includes_deepen_without_network():
    result = CliRunner().invoke(app, ["run", "topic", "--mode", "deep", "--dry-run"])

    assert result.exit_code == 0
    assert "deepen" in result.output
    assert "mode=deep" in result.output


def test_dry_run_uses_mode_from_config_when_cli_mode_is_omitted(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("pipeline:\n  mode: fast\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "mode=fast" in result.output
    assert "deepen" not in result.output


def test_dry_run_preserves_explicit_config_stages_when_mode_is_omitted(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "pipeline:\n  mode: standard\n  stages: [collect, clean, report]\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "run", "topic", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "collect → clean → report" in result.output
    assert "deepen" not in result.output
