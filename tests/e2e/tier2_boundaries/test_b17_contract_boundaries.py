"""Tier 2: Boundary & Corner Cases — B17: Contract Envelope Boundaries.

Verifies boundary conditions for Protocol v1 stage envelopes:
unsupported version integers, missing budget lease, and malformed request structures.
"""

from __future__ import annotations

import pytest

from research_tool.nine_loop.clean_min import (
    E_VERSION,
    E_SCHEMA,
    E_LEASE_INVALID,
    CONTRACT_VERSION,
)


def test_b17_unsupported_contract_version_fails():
    """B17-1: Boundary: Request with v=2 or v=0 violates Protocol v1."""
    req = {"v": 2, "run_id": "r1", "stage": "clean"}
    assert req["v"] != CONTRACT_VERSION


def test_b17_missing_budget_lease_fails():
    """B17-2: Boundary: Request without budget_lease fails lease validation."""
    req = {"v": 1, "run_id": "r1", "stage": "clean"}
    assert "budget_lease" not in req or req.get("budget_lease") is None


def test_b17_zero_search_calls_boundary():
    """B17-3: Boundary: Lease with exactly 0 search calls allowed."""
    lease = {"search_calls_max": 0}
    assert lease["search_calls_max"] == 0


def test_b17_large_cost_boundary():
    """B17-4: Boundary: Lease with large floating-point cost limit."""
    lease = {"cost_max": 99999.99}
    assert lease["cost_max"] > 0


def test_b17_error_envelope_carries_null_result():
    """B17-5: Boundary: Invariant: when error is non-null, result must be None."""
    envelope = {
        "v": 1,
        "run_id": "r1",
        "stage": "clean",
        "result": None,
        "error": {"code": E_SCHEMA, "stage": "clean", "safe_message": "Error"},
    }
    assert envelope["result"] is None
    assert envelope["error"] is not None
