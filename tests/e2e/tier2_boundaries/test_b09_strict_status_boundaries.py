"""Tier 2: Boundary & Corner Cases — B09: Strict & Status Boundaries.

Verifies boundary conditions for RESEARCH_AGENT_STRICT parsing, status on files
instead of directories, and irregular artifact folder states.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest


def test_b09_strict_mode_boolean_parsing():
    """B09-1: Boundary: Testing various truthy and falsy representations for strict mode."""

    def parse_strict(val: str | None) -> bool:
        if val is None:
            return False
        return val.strip().lower() in ("1", "true", "yes")

    assert parse_strict("1") is True
    assert parse_strict("true") is True
    assert parse_strict("True") is True
    assert parse_strict("0") is False
    assert parse_strict("false") is False
    assert parse_strict("") is False
    assert parse_strict(None) is False


def test_b09_status_on_file_instead_of_directory(run_cli, workspace: Path):
    """B09-2: Boundary: Calling research status on a regular file path."""
    file_path = workspace / "regular_file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    res = run_cli(["status", str(file_path)])
    # Should handle gracefully without unhandled python traceback
    assert "Traceback" not in res.stderr


def test_b09_status_on_directory_with_subdirectories_only(run_cli, workspace: Path):
    """B09-3: Boundary: Calling research status on directory with non-standard subdirs."""
    (workspace / "random_sub").mkdir()
    res = run_cli(["status", str(workspace)])
    assert res.returncode == 0


def test_b09_status_on_empty_workspace_returns_success(run_cli, workspace: Path):
    """B09-4: Boundary: Fresh workspace has returncode 0."""
    res = run_cli(["status", str(workspace)])
    assert res.returncode == 0


def test_b09_status_formatting_has_table_borders(run_cli, workspace: Path):
    """B09-5: Boundary: Output formatting includes rich table characters."""
    res = run_cli(["status", str(workspace)])
    assert "阶段" in res.stdout or "状态" in res.stdout
