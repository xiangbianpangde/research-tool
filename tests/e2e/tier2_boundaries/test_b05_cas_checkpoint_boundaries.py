"""Tier 2: Boundary & Corner Cases — B05: CAS Checkpoint Boundaries.

Verifies boundary conditions for atomic CAS and checkpoint recovery:
missing artifact when in state.done, malformed state.json, empty artifacts dir.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import ChainState, E2EFault, E_STATE


def test_b05_state_load_when_no_state_file_returns_clean_dict(workspace: Path):
    """B05-1: Boundary: Loading state on fresh directory returns valid default structure."""
    state_mgr = ChainState(workspace)
    state = state_mgr.load("fresh_key_0000000000000000000000000000000000000000000000000000000000")
    assert state["version"] == 1
    assert state["done"] == []
    assert state["stages"] == {}


def test_b05_read_stage_missing_artifact_raises_e_state(workspace: Path):
    """B05-2: Boundary: read_stage for an artifact file that does not exist raises E_STATE."""
    state_mgr = ChainState(workspace)
    fake_state = {"version": 1, "done": ["collect"], "stages": {"collect": "fake_hash"}}
    with pytest.raises(E2EFault) as exc_info:
        state_mgr.read_stage(fake_state, "collect")
    assert exc_info.value.code == E_STATE
    assert "missing artifact" in exc_info.value.safe_message


def test_b05_empty_state_json_file_handled(workspace: Path):
    """B05-3: Boundary: state.json is 0 bytes (empty file)."""
    state_mgr = ChainState(workspace)
    (workspace / "state.json").write_bytes(b"")
    with pytest.raises(Exception):
        state_mgr.load("test_key")


def test_b05_tampered_json_format_in_artifact_file(workspace: Path, sample_collect_envelope):
    """B05-4: Boundary: Artifact file contains non-JSON bytes."""
    state_mgr = ChainState(workspace)
    state = state_mgr.load(sample_collect_envelope["idempotency_key"])
    state = state_mgr.commit_stage(state, "collect", sample_collect_envelope)

    # Overwrite with non-JSON
    art = workspace / "artifacts" / "collect.json"
    art.write_bytes(b"NOT_JSON_DATA")

    with pytest.raises(E2EFault) as exc_info:
        state_mgr.read_stage(state, "collect")
    assert exc_info.value.code == E_STATE


def test_b05_multiple_consecutive_commits_update_state_monotonically(workspace: Path, sample_collect_envelope, sample_clean_envelope):
    """B05-5: Boundary: Committing two stages consecutively updates done list monotonically."""
    state_mgr = ChainState(workspace)
    key = sample_collect_envelope["idempotency_key"]
    sample_clean_envelope["idempotency_key"] = key

    state = state_mgr.load(key)
    state = state_mgr.commit_stage(state, "collect", sample_collect_envelope)
    assert state["done"] == ["collect"]

    state = state_mgr.commit_stage(state, "clean", sample_clean_envelope)
    assert state["done"] == ["collect", "clean"]
    assert "collect" in state["stages"]
    assert "clean" in state["stages"]
