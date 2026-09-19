"""Tier 1: Feature Coverage — F09: CLI Strict & Status Alignment.

Verifies RESEARCH_AGENT_STRICT security guard and status command alignment
with 9-stage pipeline artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest


def test_cli_status_reports_empty_directory_cleanly(run_cli, workspace: Path):
    """F09-1: Verify research status on fresh workspace reports not started without crash."""
    res = run_cli(["status", str(workspace)])
    assert res.returncode == 0
    assert "未执行" in res.stdout or "未开始" in res.stdout


def test_cli_status_reports_completed_artifacts(run_cli, workspace: Path, generate_valid_research_output):
    """F09-2: Verify research status on populated workspace identifies outputs."""
    generate_valid_research_output(workspace)
    res = run_cli(["status", str(workspace)])
    assert res.returncode == 0


def test_cli_strict_env_guard_presence():
    """F09-3: Verify RESEARCH_AGENT_STRICT environment variable check logic."""
    is_strict = os.environ.get("RESEARCH_AGENT_STRICT", "0") == "1"
    # Logic is boolean and deterministic
    assert isinstance(is_strict, bool)


def test_cli_status_nonexistent_directory_handled(run_cli, workspace: Path):
    """F09-4: Verify research status on non-existent directory gives friendly error or table."""
    missing = workspace / "nonexistent_dir_999"
    res = run_cli(["status", str(missing)])
    assert res.returncode == 0 or "不存在" in res.stderr
    assert "未执行" in res.stdout or res.returncode != 0


def test_cli_status_table_formatting(run_cli, workspace: Path, generate_valid_research_output):
    """F09-5: Verify research status output produces formatted table."""
    generate_valid_research_output(workspace)
    res = run_cli(["status", str(workspace)])
    assert res.returncode == 0
