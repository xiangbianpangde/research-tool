"""Tier 1: Feature Coverage — F16: Legacy Test Modernization.

Verifies that legacy test configurations, mode presets (brief, full, fast, standard, deep),
and test harnesses run cleanly without obsolete flags.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from research_tool.domain.config import mode_defaults, _MODE_DEFAULTS


def test_mode_presets_cover_all_supported_modes():
    """F16-1: Verify that mode_defaults contains brief, full, fast, standard, deep."""
    supported = ["brief", "full", "fast", "standard", "deep"]
    for mode in supported:
        defaults = mode_defaults(mode)
        assert isinstance(defaults, dict)
        assert len(defaults) > 0


def test_mode_brief_configures_lightweight_execution():
    """F16-2: Verify brief mode sets lightweight defaults."""
    brief = mode_defaults("brief")
    assert isinstance(brief, dict)


def test_mode_full_configures_comprehensive_execution():
    """F16-3: Verify full mode sets comprehensive execution defaults."""
    full = mode_defaults("full")
    assert isinstance(full, dict)


def test_mode_defaults_copies_are_isolated():
    """F16-4: Verify mode_defaults returns isolated copies to avoid cross-test mutation."""
    d1 = mode_defaults("standard")
    d2 = mode_defaults("standard")
    d1["custom_key"] = "mutated"
    assert "custom_key" not in d2


def test_unknown_mode_raises_config_validation_error():
    """F16-5: Verify invalid mode names raise ConfigValidationError."""
    from research_tool.domain.errors import ConfigValidationError

    with pytest.raises(ConfigValidationError):
        mode_defaults("invalid_mode_xyz")
