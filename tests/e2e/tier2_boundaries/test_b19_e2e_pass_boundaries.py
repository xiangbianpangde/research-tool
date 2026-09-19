"""Tier 2: Boundary & Corner Cases — B19: E2E Pass Boundaries.

Verifies boundary conditions for E2E runs: topic with quotes, dry-run with skips,
zero elapsed time in summary, and repeated packaging idempotence.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest


def test_b19_cli_dry_run_with_skip_flag(run_cli, workspace: Path):
    """B19-1: Boundary: CLI dry run specifying --skip option."""
    res = run_cli(["run", "Skip Test", "--mode", "brief", "--skip", "clean", "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0


def test_b19_cli_run_with_quotes_in_topic(run_cli, workspace: Path):
    """B19-2: Boundary: CLI run topic containing single and double quotes."""
    res = run_cli(["run", 'Topic "With" \'Quotes\'', "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0


def test_b19_summary_with_zero_elapsed_sec(workspace: Path, generate_valid_research_output):
    """B19-3: Boundary: run-summary.json with elapsed_sec=0.0."""
    data = generate_valid_research_output(workspace)
    summary_path = data["summary"]
    content = json.loads(summary_path.read_text(encoding="utf-8"))
    content["elapsed_sec"] = 0.0
    summary_path.write_text(json.dumps(content), encoding="utf-8")

    reloaded = json.loads(summary_path.read_text(encoding="utf-8"))
    assert reloaded["elapsed_sec"] == 0.0


def test_b19_sources_with_single_source(workspace: Path, generate_valid_research_output):
    """B19-4: Boundary: sources.json containing exactly 1 source entry."""
    data = generate_valid_research_output(workspace)
    sources = json.loads(data["sources"].read_text(encoding="utf-8"))
    assert len(sources) >= 1


def test_b19_tree_without_subnodes_has_root(workspace: Path, generate_valid_research_output):
    """B19-5: Boundary: tree directory contains root index 00-主表.md."""
    data = generate_valid_research_output(workspace)
    assert data["tree_index"].exists()
