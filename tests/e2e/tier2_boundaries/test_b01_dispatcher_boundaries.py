"""Tier 2: Boundary & Corner Cases — B01: Dispatcher Limits & Schema Errors.

Verifies boundary conditions for 9-stage dispatcher: empty seeds, zero budget,
expired lease timestamps, malformed envelope schema, and single-seed minimal cases.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from research_tool.nine_loop.collect_stage import (
    validate_request,
    CollectStageFault,
    E_SCHEMA,
    E_LEASE_INVALID,
    CONTRACT_VERSION,
)


def _valid_request_base() -> dict:
    return {
        "v": CONTRACT_VERSION,
        "run_id": "run-b01",
        "stage": "collect",
        "request_id": "req-b01",
        "idempotency_key": "0" * 64,
        "budget_lease": {
            "lease_id": "lease-b01",
            "tokens_max": 1000,
            "cost_max": 1.0,
            "wall_s_max": 10.0,
            "search_calls_max": 5,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
        "seeds": [{"url": "https://arxiv.org/abs/2301.00001"}],
        "crawl_policy": {},
    }


def test_b01_empty_seeds_raises_schema_error():
    """B01-1: Boundary: Seeds list is empty."""
    req = _valid_request_base()
    req["seeds"] = []
    with pytest.raises(CollectStageFault) as exc_info:
        validate_request(req)
    assert exc_info.value.frame["code"] == E_SCHEMA
    assert "seeds must be a non-empty list" in exc_info.value.frame["safe_message"]


def test_b01_missing_run_id_raises_schema_error():
    """B01-2: Boundary: run_id is missing or empty string."""
    req = _valid_request_base()
    req["run_id"] = ""
    with pytest.raises(CollectStageFault) as exc_info:
        validate_request(req)
    assert exc_info.value.frame["code"] == E_SCHEMA


def test_b01_negative_budget_lease_tokens_raises_lease_invalid():
    """B01-3: Boundary: budget lease token limit is negative."""
    req = _valid_request_base()
    req["budget_lease"]["tokens_max"] = -100
    with pytest.raises(CollectStageFault) as exc_info:
        validate_request(req)
    assert exc_info.value.frame["code"] == E_LEASE_INVALID


def test_b01_invalid_idempotency_key_length():
    """B01-4: Boundary: idempotency_key is shorter than 64 hex characters."""
    req = _valid_request_base()
    req["idempotency_key"] = "short_key_123"
    with pytest.raises(CollectStageFault) as exc_info:
        validate_request(req)
    assert exc_info.value.frame["code"] == E_SCHEMA
    assert "64-hex" in exc_info.value.frame["safe_message"]


def test_b01_single_minimal_seed_passes_validation():
    """B01-5: Boundary: exactly 1 valid seed with minimal crawl policy."""
    from research_tool.nine_loop.collect_stage import compute_idempotency_key

    req = _valid_request_base()
    req["idempotency_key"] = compute_idempotency_key(req)
    # Should validate without any exceptions
    validate_request(req)
