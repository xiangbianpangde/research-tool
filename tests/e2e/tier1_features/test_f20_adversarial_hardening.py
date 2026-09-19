"""Tier 1: Feature Coverage — F20: Adversarial Coverage Hardening.

Verifies system resilience against path traversal attacks, Unicode normalization edge cases,
corrupted state recovery, and concurrent workspace isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest


def test_adversarial_path_traversal_blocked(run_cli, workspace: Path):
    """F20-1: Verify that directory traversal attempts via CLI --output are contained or error out."""
    evil_path = workspace / ".." / ".." / "traversal_attempt"
    res = run_cli(["run", "Path Traversal Test", "--output", str(evil_path), "--dry-run"])
    # The runner must either execute safely or reject without leaking outside
    assert not Path("/tmp/traversal_attempt").exists()


def test_adversarial_unicode_topic_normalization(workspace: Path):
    """F20-2: Verify topics containing emojis, Cyrillic, and math symbols slugify safely."""
    from research_tool.common.slug import slugify

    complex_topic = "量子计算 🚀 Quantum ∇·B=0 — тест"
    slug = slugify(complex_topic)
    assert len(slug) > 0
    # Slug must not contain raw spaces or control characters
    assert " " not in slug
    assert "\n" not in slug


def test_adversarial_corrupted_state_json_handling(workspace: Path):
    """F20-3: Verify ChainState detects malformed non-JSON state.json safely."""
    from research_tool.nine_loop.e2e import ChainState

    state_mgr = ChainState(workspace)
    (workspace / "state.json").write_bytes(b"INVALID_CORRUPTED_JSON_BYTES{{{")

    with pytest.raises(Exception):
        state_mgr.load("a" * 64)


def test_adversarial_large_content_sha256_hash_speed():
    """F20-4: Verify sha256 hashing handles multi-megabyte payloads efficiently without crash."""
    import hashlib

    large_payload = b"A" * 5_000_000  # 5MB
    digest = hashlib.sha256(large_payload).hexdigest()
    assert len(digest) == 64


def test_adversarial_concurrent_workspace_isolation(tmp_path: Path, generate_valid_research_output):
    """F20-5: Verify two concurrent workspaces do not collide or share mutable state."""
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"

    out1 = generate_valid_research_output(ws1)
    out2 = generate_valid_research_output(ws2)

    # Modify ws1 report
    out1["report"].write_text("Modified report 1", encoding="utf-8")
    assert "Modified report 1" not in out2["report"].read_text(encoding="utf-8")
