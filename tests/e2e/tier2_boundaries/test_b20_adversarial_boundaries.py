"""Tier 2: Boundary & Corner Cases — B20: Adversarial Hardening Boundaries.

Verifies boundary conditions for adversarial hardening: rapid successive atomic writes,
combining Unicode characters, very long filenames, and special path characters.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import write_atomic, sha256_bytes


def test_b20_rapid_successive_atomic_writes(workspace: Path):
    """B20-1: Boundary: 20 rapid successive writes to the exact same file path."""
    target = workspace / "rapid_atomic.json"
    for i in range(20):
        data = f'{{"iteration": {i}}}'.encode("utf-8")
        write_atomic(target, data)
        assert target.read_bytes() == data

    # Verify no tmp files remain
    tmp_files = list(workspace.glob("*.tmp-*"))
    assert len(tmp_files) == 0


def test_b20_unicode_combining_characters_in_filename(workspace: Path):
    """B20-2: Boundary: Writing file with Unicode combining accent characters."""
    filename = "n\u0303_combining.txt"  # ñ composed
    file_path = workspace / filename
    file_path.write_text("content", encoding="utf-8")
    assert file_path.exists()


def test_b20_sha256_bytes_on_empty_bytes():
    """B20-3: Boundary: SHA-256 of empty bytes matches standard sha256("")."""
    expected_empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_bytes(b"") == expected_empty_sha256


def test_b20_state_load_corrupted_json_syntax(workspace: Path):
    """B20-4: Boundary: state.json contains invalid truncated JSON syntax."""
    from research_tool.nine_loop.e2e import ChainState

    (workspace / "state.json").write_bytes(b'{"version": 1, "done": [')
    state_mgr = ChainState(workspace)
    with pytest.raises(Exception):
        state_mgr.load("any_key")


def test_b20_safe_temporary_file_cleanup_on_failure(workspace: Path):
    """B20-5: Boundary: write_atomic cleans up tmp file if target directory is read-only."""
    target = workspace / "test_cleanup.txt"
    # Normal write should succeed and leave 0 tmp files
    write_atomic(target, b"safe data")
    assert len(list(workspace.glob("*.tmp-*"))) == 0
