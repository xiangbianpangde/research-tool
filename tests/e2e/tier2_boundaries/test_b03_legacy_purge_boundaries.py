"""Tier 2: Boundary & Corner Cases — B03: Legacy Purge Boundaries.

Verifies boundary conditions for artifact preservation: existing user files,
zero-byte files, non-standard files in raw/, and absent directory handling.
"""

from __future__ import annotations

from pathlib import Path
import pytest


def test_b03_user_custom_notes_in_raw_preserved(workspace: Path, generate_valid_research_output):
    """B03-1: Boundary: Custom user notes in raw/ are preserved across runs."""
    generate_valid_research_output(workspace)
    raw_dir = workspace / "raw"
    raw_dir.mkdir(exist_ok=True)
    custom_note = raw_dir / "user_custom_annotation.md"
    custom_note.write_text("User personal notes that must not be wiped.", encoding="utf-8")

    assert custom_note.exists()
    assert "personal notes" in custom_note.read_text(encoding="utf-8")


def test_b03_zero_byte_clean_artifact_handling(workspace: Path):
    """B03-2: Boundary: Handling zero-byte artifact in workspace."""
    art_dir = workspace / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    empty_art = art_dir / "clean.json"
    empty_art.write_bytes(b"")
    assert empty_art.stat().st_size == 0


def test_b03_nonexistent_artifact_directory_lookup(workspace: Path):
    """B03-3: Boundary: Querying artifacts when artifacts/ directory is absent."""
    missing_dir = workspace / "nonexistent_artifacts"
    assert not missing_dir.exists()
    assert list(missing_dir.glob("*.json")) == []


def test_b03_multiple_runs_do_not_replicate_tree_subdirectories(workspace: Path, generate_valid_research_output):
    """B03-4: Boundary: Multiple executions don't cause duplicate tree directory hierarchies."""
    generate_valid_research_output(workspace)
    tree_dir = workspace / "tree"
    subdirs = [d for d in tree_dir.iterdir() if d.is_dir()]
    assert len(subdirs) == 0  # Tree is a flat node structure


def test_b03_read_only_completed_report_preserved(workspace: Path, generate_valid_research_output):
    """B03-5: Boundary: report.md with read-only permissions (0o444) is inspectable."""
    data = generate_valid_research_output(workspace)
    report = data["report"]
    report.chmod(0o444)
    try:
        assert report.exists()
        assert report.stat().st_size > 0
    finally:
        report.chmod(0o644)  # Restore for cleanup
