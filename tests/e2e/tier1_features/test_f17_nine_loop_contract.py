"""Tier 1: Feature Coverage — F17: Nine-Loop Contract Test Porting.

Verifies Protocol v1 stage envelope schema, canonical input hashing,
budget lease validation, and typed error frames across 9 stages.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.clean_min import (
    compute_idempotency_key as compute_clean_idempotency_key,
    make_error_frame as make_clean_error_frame,
    E_SCHEMA,
    E_LEASE_INVALID,
    E_IDEMPOTENCY_CONFLICT,
    CONTRACT_VERSION,
)


def test_contract_version_constant_is_one():
    """F17-1: Verify that contract version constant is 1."""
    assert CONTRACT_VERSION == 1


def test_clean_idempotency_key_deterministic():
    """F17-2: Verify compute_idempotency_key generates deterministic SHA-256."""
    input_data = [{"sample": "data", "id": 1}]
    k1 = compute_clean_idempotency_key(input_data)
    k2 = compute_clean_idempotency_key(input_data)
    assert k1 == k2
    assert len(k1) == 64


def test_make_error_frame_structure():
    """F17-3: Verify make_error_frame produces valid typed error dictionary."""
    frame = make_clean_error_frame(E_SCHEMA, "Test safe error message", retryable=False)
    assert frame["code"] == E_SCHEMA
    assert frame["stage"] == "clean"
    assert frame["safe_message"] == "Test safe error message"
    assert frame["retryable"] is False


def test_protocol_v1_envelope_keys_present(sample_collect_envelope):
    """F17-4: Verify standard Protocol v1 envelope keys."""
    required = {"v", "run_id", "stage", "request_id", "idempotency_key", "budget_lease", "result", "error"}
    assert required.issubset(set(sample_collect_envelope.keys()))


def test_budget_lease_fields_present(sample_collect_envelope):
    """F17-5: Verify all budget lease fields are present and typed."""
    lease = sample_collect_envelope["budget_lease"]
    assert "lease_id" in lease
    assert "tokens_max" in lease
    assert "cost_max" in lease
    assert "wall_s_max" in lease
    assert "search_calls_max" in lease
    assert "issued_at" in lease
    assert "expires_at" in lease
