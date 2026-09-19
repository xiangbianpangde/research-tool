"""Tier 1: Feature Coverage — F19: E2E Requirement Test Pass.

Verifies end-to-end requirement fulfillment: dry-run planning, complete artifact
materialization, and wiki staging packaging.
"""

from __future__ import annotations

from pathlib import Path
import pytest


def test_e2e_cli_dry_run_executes_cleanly(run_cli, workspace: Path):
    """F19-1: Verify CLI --dry-run prints plan without failures."""
    res = run_cli(["run", "Autonomous Agents", "--output", str(workspace), "--dry-run"])
    assert res.returncode == 0
    assert "阶段" in res.stdout or "dry-run" in res.stdout.lower() or "run" in res.stdout.lower()


def test_e2e_workspace_artifact_manifest(workspace: Path, generate_valid_research_output):
    """F19-2: Verify completed research workspace satisfies all downstream contracts."""
    data = generate_valid_research_output(workspace)

    assert data["report"].stat().st_size > 0
    assert data["tree_index"].stat().st_size > 0
    assert data["sources"].stat().st_size > 0
    assert data["summary"].stat().st_size > 0


def test_e2e_wiki_stage_cli_command(run_cli, workspace: Path, generate_valid_research_output, tmp_path: Path):
    """F19-3: Verify research wiki-stage CLI command packages output."""
    generate_valid_research_output(workspace)
    dest = tmp_path / "wiki_pkg_out"
    dest.mkdir()

    res = run_cli(["wiki-stage", str(workspace), "--package-output", str(dest), "--build"])
    assert res.returncode == 0
    assert "package" in res.stdout.lower() or "rp_" in res.stdout or "打包" in res.stdout


def test_e2e_state_manifest_integrity(workspace: Path, generate_valid_research_output):
    """F19-4: Verify state.json records 9-stage completion status."""
    import json

    generate_valid_research_output(workspace)
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    assert len(state["done"]) == 9
    assert len(state["stages"]) == 9


def test_e2e_report_markdown_has_source_citations(workspace: Path, generate_valid_research_output):
    """F19-5: Verify report.md contains explicit source citations."""
    data = generate_valid_research_output(workspace)
    report_text = data["report"].read_text(encoding="utf-8")
    assert "[^1]" in report_text or "source:" in report_text
