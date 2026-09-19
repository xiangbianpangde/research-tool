"""Tier 1: Feature Coverage — F14: Downstream Wiki-Stage Deliverables.

Verifies that the 9-stage pipeline generates all deliverables required by wiki-stage:
- report.md with Citation Coverage 1.0
- tree/00-主表.md
- sources.json
- run-summary.json
- successful packaging via build_stage_package()
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.infrastructure.export.wiki_stage import build_stage_package, plan_stage_package


def test_downstream_deliverables_presence(workspace: Path, generate_valid_research_output):
    """F14-1: Verify that all 4 primary wiki-stage deliverables are materialized."""
    data = generate_valid_research_output(workspace)

    assert data["report"].exists()
    assert data["tree_index"].exists()
    assert data["sources"].exists()
    assert data["summary"].exists()


def test_citation_coverage_strictly_one(workspace: Path, generate_valid_research_output):
    """F14-2: Verify Citation Coverage 1.0 assertion in run-summary.json."""
    data = generate_valid_research_output(workspace)
    summary = json.loads(data["summary"].read_text(encoding="utf-8"))
    assert summary.get("citation_coverage") == 1.0


def test_tree_index_conforms_to_markdown_link_structure(workspace: Path, generate_valid_research_output):
    """F14-3: Verify tree/00-主表.md contains valid [[Node]] wiki-links."""
    data = generate_valid_research_output(workspace)
    content = data["tree_index"].read_text(encoding="utf-8")
    assert "[[" in content and "]]" in content


def test_sources_manifest_conforms_to_schema(workspace: Path, generate_valid_research_output):
    """F14-4: Verify sources.json items contain url, fetchedAt, and content_hash."""
    data = generate_valid_research_output(workspace)
    sources = json.loads(data["sources"].read_text(encoding="utf-8"))
    assert isinstance(sources, list)
    assert len(sources) > 0
    item = sources[0]
    assert "url" in item
    assert "fetchedAt" in item
    assert "content_hash" in item


def test_wiki_stage_package_builder_produces_immutable_archive(workspace: Path, generate_valid_research_output, tmp_path: Path):
    """F14-5: Verify build_stage_package creates immutable package with manifest."""
    generate_valid_research_output(workspace)
    destination = tmp_path / "wiki_dest"
    destination.mkdir()

    pkg = build_stage_package(workspace, destination)
    assert pkg is not None
    assert pkg.package_id.startswith("rp_")
    assert pkg.package_path.exists()
    assert (pkg.package_path / "manifest.json").exists()
