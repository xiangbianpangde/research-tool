"""Tier 2: Boundary & Corner Cases — B16: Legacy Test Boundaries.

Verifies boundary conditions for modernized legacy test runners, mode configs,
and parameter boundaries across brief/full/fast/standard/deep presets.
"""

from __future__ import annotations

import pytest
from research_tool.domain.config import mode_defaults


def test_b16_all_standard_modes_contain_non_empty_presets():
    """B16-1: Boundary: Every valid mode has non-empty presets."""
    for mode in ("brief", "full", "fast", "standard", "deep"):
        preset = mode_defaults(mode)
        assert len(preset) > 0


def test_b16_mode_defaults_idempotent_invocation():
    """B16-2: Boundary: Repeated calls to mode_defaults return equal dictionaries."""
    p1 = mode_defaults("brief")
    p2 = mode_defaults("brief")
    assert p1 == p2


def test_b16_whitespace_in_mode_lookup_fails():
    """B16-3: Boundary: Mode string with whitespace raises ConfigValidationError."""
    from research_tool.domain.errors import ConfigValidationError

    with pytest.raises(ConfigValidationError):
        mode_defaults(" brief ")


def test_b16_cased_mode_lookup_fails():
    """B16-4: Boundary: Upper-cased mode string raises ConfigValidationError."""
    from research_tool.domain.errors import ConfigValidationError

    with pytest.raises(ConfigValidationError):
        mode_defaults("BRIEF")


def test_b16_mode_mutation_does_not_leak_to_globals():
    """B16-5: Boundary: Mutating a returned mode dictionary does not alter global defaults."""
    mod = mode_defaults("fast")
    mod["collector"] = {"corrupted": True}

    fresh = mode_defaults("fast")
    assert "corrupted" not in fresh.get("collector", {})
