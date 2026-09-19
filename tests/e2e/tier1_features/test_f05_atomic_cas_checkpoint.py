"""Tier 1: Feature Coverage — F05: Atomic CAS & Checkpointing.

Verifies that ChainState commits artifacts atomically under artifacts/{stage}.json + state.json
with SHA-256 verification and enables idempotent --resume recovery.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.e2e import (
    ChainState,
    E2EFault,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    write_atomic,
    sha256_bytes,
)


def test_chain_state_atomic_commit_writes_file_and_state(workspace: Path, sample_collect_envelope):
    """F05-1: Verify that commit_stage writes stage.json and updates state.json with matching SHA-256."""
    state_mgr = ChainState(workspace)
    initial_state = state_mgr.load(sample_collect_envelope["idempotency_key"])

    committed_state = state_mgr.commit_stage(initial_state, "collect", sample_collect_envelope)

    artifact_file = workspace / "artifacts" / "collect.json"
    assert artifact_file.exists()

    expected_digest = sha256_bytes(artifact_file.read_bytes())
    assert committed_state["stages"]["collect"] == expected_digest
    assert "collect" in committed_state["done"]

    # Verify persisted state.json on disk
    persisted_state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    assert persisted_state["stages"]["collect"] == expected_digest
    assert "collect" in persisted_state["done"]


def test_chain_state_verifies_sha256_digests_on_load(workspace: Path, sample_collect_envelope):
    """F05-2: Verify that read_stage validates the artifact against the SHA-256 digest in state."""
    state_mgr = ChainState(workspace)
    state = state_mgr.load(sample_collect_envelope["idempotency_key"])
    state = state_mgr.commit_stage(state, "collect", sample_collect_envelope)

    read_back = state_mgr.read_stage(state, "collect")
    assert read_back["stage"] == "collect"
    assert read_back["run_id"] == sample_collect_envelope["run_id"]


def test_resume_mode_detects_corrupted_artifact_digest(workspace: Path, sample_collect_envelope):
    """F05-3: Verify that tampering with artifacts/{stage}.json raises E_STATE."""
    state_mgr = ChainState(workspace)
    state = state_mgr.load(sample_collect_envelope["idempotency_key"])
    state = state_mgr.commit_stage(state, "collect", sample_collect_envelope)

    # Tamper with the artifact file
    artifact_file = workspace / "artifacts" / "collect.json"
    artifact_file.write_bytes(b'{"corrupted": true}')

    with pytest.raises(E2EFault) as exc_info:
        state_mgr.read_stage(state, "collect")
    assert exc_info.value.code == E_STATE


def test_resume_mode_detects_idempotency_conflict(workspace: Path, sample_collect_envelope):
    """F05-4: Verify that attempting to resume with a different idempotency key raises E_IDEMPOTENCY_CONFLICT."""
    state_mgr = ChainState(workspace)
    state = state_mgr.load("key_alpha_111111111111111111111111111111111111111111111111111111111111")
    state_mgr.commit_stage(state, "collect", sample_collect_envelope)

    # Try to load with a mismatched input key
    with pytest.raises(E2EFault) as exc_info:
        state_mgr.load("key_beta_222222222222222222222222222222222222222222222222222222222222")
    assert exc_info.value.code == E_IDEMPOTENCY_CONFLICT


def test_write_atomic_creates_destination_without_tmp_residue(workspace: Path):
    """F05-5: Verify write_atomic replaces target atomically and leaves zero temporary files."""
    target_file = workspace / "atomic_test.json"
    payload = b'{"atomic": "ok"}'

    write_atomic(target_file, payload)

    assert target_file.exists()
    assert target_file.read_bytes() == payload
    tmp_residue = list(workspace.glob("*.tmp-*"))
    assert len(tmp_residue) == 0
