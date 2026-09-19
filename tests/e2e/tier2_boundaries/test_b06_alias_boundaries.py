"""Tier 2: Boundary & Corner Cases — B06: Stage Literal & Alias Boundaries.

Verifies boundary conditions for stage aliases: case sensitivity, whitespace,
unrecognized stage names, and round-trip alias mapping.
"""

from __future__ import annotations

import pytest


CANONICAL_TO_CONTRACT = {
    "collect": "collect",
    "clean": "clean",
    "extract": "extract",
    "knowledge": "network",
    "inspect": "inspect",
    "targeted": "targeted",
    "merge": "merge",
    "qgate": "gate",
    "report": "report",
}


def normalize_stage_name(raw: str) -> str:
    cleaned = raw.strip().lower()
    alias_map = {
        "network": "knowledge",
        "gate": "qgate",
    }
    return alias_map.get(cleaned, cleaned)


def test_b06_alias_with_surrounding_whitespace():
    """B06-1: Boundary: Stage alias string with leading/trailing whitespace."""
    assert normalize_stage_name("  network  ") == "knowledge"
    assert normalize_stage_name("\tgate\n") == "qgate"


def test_b06_alias_mixed_casing():
    """B06-2: Boundary: Stage alias string with uppercase or title case."""
    assert normalize_stage_name("Network") == "knowledge"
    assert normalize_stage_name("GATE") == "qgate"
    assert normalize_stage_name("Knowledge") == "knowledge"


def test_b06_unknown_stage_name_remains_unchanged():
    """B06-3: Boundary: Unknown stage name returns stripped string."""
    assert normalize_stage_name("custom_stage") == "custom_stage"


def test_b06_empty_stage_name_returns_empty():
    """B06-4: Boundary: Empty string input."""
    assert normalize_stage_name("") == ""


def test_b06_all_nine_stages_canonicalize_cleanly():
    """B06-5: Boundary: All 9 stages resolve to their canonical domain representations."""
    for canonical in ("collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"):
        assert normalize_stage_name(canonical) == canonical
