"""Tier 2: Boundary & Corner Cases — B14: Downstream Wiki-Stage Boundaries.

Verifies boundary conditions for wiki packaging: symlink rejection, secret detection,
missing mandatory files, and destination collisions.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.infrastructure.export.wiki_stage import (
    build_stage_package,
    PackageStageError,
)


def test_b14_packaging_rejects_symlink_source(workspace: Path, tmp_path: Path):
    """B14-1: Boundary: Packaging fails if source is a symlink."""
    link_dir = tmp_path / "symlink_source"
    try:
        link_dir.symlink_to(workspace)
        dest = tmp_path / "dest1"
        dest.mkdir()
        with pytest.raises(PackageStageError):
            build_stage_package(link_dir, dest)
    except OSError:
        pass


def test_b14_packaging_detects_secrets(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """B14-2: Boundary: Packaging fails if secret patterns (e.g. PRIVATE KEY) are present."""
    generate_valid_research_output(workspace)
    leak_file = workspace / "secret_leak.txt"
    leak_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...", encoding="utf-8")

    dest = tmp_path / "dest2"
    dest.mkdir()
    with pytest.raises(PackageStageError) as exc_info:
        build_stage_package(workspace, dest)
    assert "secret" in str(exc_info.value).lower()


def test_b14_nonexistent_source_directory(tmp_path: Path):
    """B14-3: Boundary: Packaging fails when source directory does not exist."""
    missing = tmp_path / "missing_source"
    dest = tmp_path / "dest3"
    dest.mkdir()
    with pytest.raises(PackageStageError):
        build_stage_package(missing, dest)


def test_b14_destination_is_file_not_dir(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """B14-4: Boundary: Packaging fails if destination is an existing file."""
    generate_valid_research_output(workspace)
    dest_file = tmp_path / "dest_file.txt"
    dest_file.write_text("i am a file", encoding="utf-8")
    with pytest.raises((PackageStageError, FileExistsError, OSError)):
        build_stage_package(workspace, dest_file)


def test_b14_already_packaged_returns_already_exists(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """B14-5: Boundary: Re-packaging identical source into same destination reports already_exists=True."""
    generate_valid_research_output(workspace)
    dest = tmp_path / "dest5"
    dest.mkdir()
    pkg1 = build_stage_package(workspace, dest)
    assert pkg1.already_exists is False

    pkg2 = build_stage_package(workspace, dest)
    assert pkg2.already_exists is True
    assert pkg2.package_id == pkg1.package_id
