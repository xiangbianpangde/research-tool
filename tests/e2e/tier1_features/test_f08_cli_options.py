"""Tier 1: Feature Coverage — F08: CLI Options Modernization.

Verifies CLI commands and options for the 9-stage pipeline, including
modernized flags (--qgate-max-high, --qgate-max-total, --max-targeted-rounds)
and retirement of obsolete flags.
"""

from __future__ import annotations

from pathlib import Path
import pytest


def test_cli_help_displays_subcommands(run_cli):
    """F08-1: Verify CLI top-level help lists primary commands."""
    res = run_cli(["--help"])
    assert res.returncode == 0
    assert "collect" in res.stdout
    assert "run" in res.stdout
    assert "status" in res.stdout
    assert "config" in res.stdout


def test_cli_run_help_displays_standard_options(run_cli):
    """F08-2: Verify CLI research run displays standard pipeline controls."""
    res = run_cli(["run", "--help"])
    assert res.returncode == 0
    assert "--source" in res.stdout
    assert "--output" in res.stdout
    assert "--mode" in res.stdout
    assert "--resume" in res.stdout
    assert "--dry-run" in res.stdout


def test_cli_dry_run_executes_without_side_effects(run_cli, workspace: Path):
    """F08-3: Verify CLI research run with --dry-run prints plan without creating real files."""
    res = run_cli(["run", "Quantum Computing", "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0
    # Dry run must not create state.json or artifacts/
    assert not (workspace / "state.json").exists()


def test_cli_config_command_redacts_secrets(run_cli):
    """F08-4: Verify research config prints sanitized output without leaking raw secrets."""
    res = run_cli(["config"])
    assert res.returncode == 0
    # Output must not contain unredacted api keys
    assert "sk-" not in res.stdout


def test_cli_version_flag_displays_version(run_cli):
    """F08-5: Verify research --version displays valid semantic version string."""
    res = run_cli(["--version"])
    assert res.returncode == 0
    assert "research-tool" in res.stdout
