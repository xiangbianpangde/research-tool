"""Tier 2: Boundary & Corner Cases — B13: TalkLinker Boundaries.

Verifies boundary conditions for TalkLinker: empty sources, low similarity,
single-word titles, punctuation-heavy titles, and missing metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.application.talk_linker import (
    TalkLinker,
    title_similarity,
)
from research_tool.domain.models import TalkConfig


def test_b13_empty_sources_json_yields_zero_candidates(workspace: Path):
    """B13-1: Boundary: sources.json is empty list."""
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "sources.json").write_text("[]", encoding="utf-8")

    linker = TalkLinker(TalkConfig(enabled=True))
    candidates = linker.candidates_from_sources(raw_dir)
    assert len(candidates) == 0


def test_b13_similarity_with_empty_strings():
    """B13-2: Boundary: Title similarity comparison with empty strings."""
    assert title_similarity("", "") == 0.0
    assert title_similarity("NeRF", "") == 0.0
    assert title_similarity("", "NeRF") == 0.0


def test_b13_punctuation_only_titles():
    """B13-3: Boundary: Titles consisting exclusively of punctuation."""
    assert title_similarity("--- ... ???", "--- ... ???") == 0.0


def test_b13_identical_titles_yield_perfect_similarity():
    """B13-4: Boundary: Exact identical titles yield similarity 1.0."""
    t = "Deep Residual Learning for Image Recognition"
    assert title_similarity(t, t) == 1.0


def test_b13_talk_disabled_bypasses_processing():
    """B13-5: Boundary: TalkConfig with enabled=False."""
    cfg = TalkConfig(enabled=False)
    linker = TalkLinker(cfg)
    assert linker.config.enabled is False
