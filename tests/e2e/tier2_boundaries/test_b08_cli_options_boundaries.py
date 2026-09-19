"""Tier 2: Boundary & Corner Cases — B08: CLI Options Boundaries.

Verifies boundary conditions for CLI options: missing arguments, multiple flags,
unrecognized subcommands, and very long string arguments.
"""

from __future__ import annotations

from pathlib import Path
import pytest


def test_b08_missing_command_shows_help(run_cli):
    """B08-1: Boundary: CLI invoked with no arguments shows help."""
    res = run_cli([])
    assert res.returncode == 0 or res.returncode == 2
    assert "Usage" in res.stdout or "Usage" in res.stderr or "help" in res.stdout.lower()


def test_b08_unknown_subcommand_fails(run_cli):
    """B08-2: Boundary: Unknown subcommand results in non-zero exit."""
    res = run_cli(["unknown-command-xyz"])
    assert res.returncode != 0
    assert "No such command" in res.stderr or "Error" in res.stderr or res.returncode == 2


def test_b08_very_long_topic_argument(run_cli, workspace: Path):
    """B08-3: Boundary: Passing a 500-character topic string to dry run."""
    long_topic = "Quantum " * 60
    res = run_cli(["run", long_topic, "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0


def test_b08_multiple_sources_specified(run_cli, workspace: Path):
    """B08-4: Boundary: Specifying multiple source flags (-s web -s arxiv)."""
    res = run_cli(["run", "Multisource Test", "-s", "web", "-s", "arxiv", "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0


def test_b08_invalid_numeric_flag_rejected(run_cli, workspace: Path):
    """B08-5: Boundary: Passing string to integer option like --max-results."""
    res = run_cli(["run", "Type Error Test", "-n", "not_a_number", "--output", str(workspace)])
    assert res.returncode != 0
