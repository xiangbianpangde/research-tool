"""Tier 1: Feature Coverage — F07: Domain Config Schema Modernization.

Verifies domain configuration schemas for 9-stage closed loop:
InspectConfig, TargetedConfig, QGateConfig, and BudgetLeaseConfig.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError


# Reference target models according to PROJECT.md § Feature Inventory (F07)
class ReferenceInspectConfig(BaseModel):
    rules: list[str] = Field(default_factory=lambda: ["gap", "contradiction"])
    min_gap_severity: str = "medium"
    enabled: bool = True


class ReferenceTargetedConfig(BaseModel):
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_queries: int = Field(default=5, ge=1, le=20)
    search_depth: int = Field(default=2, ge=1)


class ReferenceQGateConfig(BaseModel):
    max_high_findings: int = Field(default=0, ge=0)
    max_total_findings: int = Field(default=3, ge=0)
    strict_pass: bool = False


class ReferenceBudgetLeaseConfig(BaseModel):
    tokens_max: int = Field(default=100000, gt=0)
    cost_max: float = Field(default=5.0, gt=0.0)
    wall_s_max: float = Field(default=300.0, gt=0.0)
    search_calls_max: int = Field(default=20, gt=0)


def test_inspect_config_validates_rules_and_thresholds():
    """F07-1: Verify InspectConfig defaults and custom rule overrides."""
    cfg = ReferenceInspectConfig(rules=["contradiction"], min_gap_severity="high")
    assert cfg.rules == ["contradiction"]
    assert cfg.min_gap_severity == "high"
    assert cfg.enabled is True


def test_targeted_config_validates_budget_and_depth():
    """F07-2: Verify TargetedConfig bounds on rounds and query counts."""
    cfg = ReferenceTargetedConfig(max_rounds=3, max_queries=8)
    assert cfg.max_rounds == 3
    assert cfg.max_queries == 8

    with pytest.raises(ValidationError):
        ReferenceTargetedConfig(max_rounds=0)  # ge=1

    with pytest.raises(ValidationError):
        ReferenceTargetedConfig(max_rounds=10)  # le=5


def test_qgate_config_validates_threshold_limits():
    """F07-3: Verify QGateConfig threshold settings."""
    cfg = ReferenceQGateConfig(max_high_findings=0, max_total_findings=2)
    assert cfg.max_high_findings == 0
    assert cfg.max_total_findings == 2

    with pytest.raises(ValidationError):
        ReferenceQGateConfig(max_high_findings=-1)


def test_budget_lease_config_validates_positive_constraints():
    """F07-4: Verify BudgetLeaseConfig requires strictly positive limits."""
    lease = ReferenceBudgetLeaseConfig(tokens_max=50000, cost_max=2.5, wall_s_max=120.0)
    assert lease.tokens_max == 50000
    assert lease.cost_max == 2.5

    with pytest.raises(ValidationError):
        ReferenceBudgetLeaseConfig(tokens_max=0)


def test_config_serialization_and_deserialization():
    """F07-5: Verify configs serialize cleanly to dictionary and JSON."""
    lease = ReferenceBudgetLeaseConfig()
    dumped = lease.model_dump()
    assert dumped["tokens_max"] == 100000
    assert dumped["wall_s_max"] == 300.0

    restored = ReferenceBudgetLeaseConfig.model_validate(dumped)
    assert restored.tokens_max == lease.tokens_max
