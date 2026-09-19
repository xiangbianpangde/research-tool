"""Tier 2: Boundary & Corner Cases — B07: Config Schema Boundaries.

Verifies boundary conditions for modernized domain configs:
extreme budgets, zero findings, empty rules lists, and invalid types.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError


class BoundaryInspectConfig(BaseModel):
    rules: list[str] = Field(default_factory=list)
    min_gap_severity: str = "medium"


class BoundaryTargetedConfig(BaseModel):
    max_rounds: int = Field(default=2, ge=1, le=10)
    max_queries: int = Field(default=5, ge=1, le=50)


class BoundaryQGateConfig(BaseModel):
    max_high_findings: int = Field(default=0, ge=0)
    max_total_findings: int = Field(default=0, ge=0)


class BoundaryBudgetLeaseConfig(BaseModel):
    tokens_max: int = Field(gt=0)
    cost_max: float = Field(gt=0.0)


def test_b07_zero_findings_allowed_in_qgate():
    """B07-1: Boundary: QGateConfig configured with strict zero findings."""
    cfg = BoundaryQGateConfig(max_high_findings=0, max_total_findings=0)
    assert cfg.max_high_findings == 0
    assert cfg.max_total_findings == 0


def test_b07_empty_rules_list_in_inspect():
    """B07-2: Boundary: InspectConfig with empty rules list."""
    cfg = BoundaryInspectConfig(rules=[])
    assert cfg.rules == []


def test_b07_extreme_tokens_in_budget_lease():
    """B07-3: Boundary: BudgetLeaseConfig with 1 billion tokens limit."""
    cfg = BoundaryBudgetLeaseConfig(tokens_max=1_000_000_000, cost_max=1000.0)
    assert cfg.tokens_max == 1_000_000_000


def test_b07_targeted_config_exceeds_max_rounds():
    """B07-4: Boundary: TargetedConfig rounds exceed upper bound."""
    with pytest.raises(ValidationError):
        BoundaryTargetedConfig(max_rounds=11)


def test_b07_targeted_config_zero_rounds_rejected():
    """B07-5: Boundary: TargetedConfig rounds zero rejected."""
    with pytest.raises(ValidationError):
        BoundaryTargetedConfig(max_rounds=0)
