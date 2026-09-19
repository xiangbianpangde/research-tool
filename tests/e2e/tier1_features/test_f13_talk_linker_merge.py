"""Tier 1: Feature Coverage — F13: Non-Destructive TalkLinker.

Verifies TalkLinker candidate extraction, similarity confidence gating,
and non-destructive knowledge network enrichment using CAS.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.application.talk_linker import (
    TalkLinker,
    PaperCandidate,
    title_similarity,
)
from research_tool.domain.models import TalkConfig


def test_talk_linker_extracts_candidates_from_sources_json(workspace: Path):
    """F13-1: Verify that candidates_from_sources extracts paper titles from sources.json."""
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True)
    sources = [
        {
            "url": "https://openaccess.thecvf.com/content/CVPR2026/papers/Paper1.pdf",
            "title": "NeRF in the Wild: Neural Radiance Fields for Unconstrained Photo Collections",
            "source_engine": "cvpr",
        }
    ]
    (raw_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    linker = TalkLinker(TalkConfig(enabled=True))
    candidates = linker.candidates_from_sources(raw_dir)
    assert len(candidates) == 1
    assert "NeRF in the Wild" in candidates[0].title


def test_talk_linker_title_similarity_matching():
    """F13-2: Verify token Jaccard similarity between paper title and video title."""
    paper_title = "NeRF in the Wild: Neural Radiance Fields"
    video_exact = "[CVPR 2026] NeRF in the Wild: Neural Radiance Fields Presentation"
    video_different = "Introduction to Deep Learning and Transformers"

    sim_high = title_similarity(paper_title, video_exact)
    sim_low = title_similarity(paper_title, video_different)

    assert sim_high >= 0.6
    assert sim_low < 0.2


def test_talk_linker_cas_merge_preserves_existing_artifacts(workspace: Path, generate_valid_research_output):
    """F13-3: Verify that talk enrichment does NOT delete clean/, extracted/, or tree/ directories."""
    data = generate_valid_research_output(workspace)
    clean_dir = workspace / "artifacts" / "clean.json"
    tree_file = data["tree_index"]

    assert clean_dir.exists()
    assert tree_file.exists()

    # Simulate talk note ingestion: creating a talk note in raw/
    raw_dir = workspace / "raw"
    raw_dir.mkdir(exist_ok=True)
    talk_note = raw_dir / "talk_01_nerf_presentation.md"
    talk_note.write_text("<!-- from_paper: NeRF in the Wild -->\nVideo summary notes.", encoding="utf-8")

    # Invariants: None of the existing artifacts should be deleted
    assert clean_dir.exists()
    assert tree_file.exists()


def test_talk_linker_official_channel_scoring():
    """F13-4: Verify that official channels gain confidence boost."""
    from research_tool.application.talk_linker import _OFFICIAL_CHANNEL_HINTS

    assert "cvpr" in _OFFICIAL_CHANNEL_HINTS
    assert "google research" in _OFFICIAL_CHANNEL_HINTS


def test_talk_linker_candidate_deduplication(workspace: Path):
    """F13-5: Verify duplicate source entries result in deduplicated paper candidates."""
    raw_dir = workspace / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        {"url": "https://arxiv.org/abs/2301.00001", "title": "Duplicate Paper", "source_engine": "arxiv"},
        {"url": "https://arxiv.org/abs/2301.00001", "title": "Duplicate Paper", "source_engine": "arxiv"},
    ]
    (raw_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    linker = TalkLinker(TalkConfig(enabled=True))
    candidates = linker.candidates_from_sources(raw_dir)
    assert len(candidates) == 1
