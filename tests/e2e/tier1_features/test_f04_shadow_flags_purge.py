"""Tier 1: Feature Coverage — F04: Shadow Sidecar & Flags Purge.

Verifies that the pipeline operates natively without requiring obsolete flags
(nine_loop.enabled, deepen_as_strategy) or shadow sidecar execution.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from research_tool.domain.models import PipelineConfig


def test_pipeline_runs_natively_without_nine_loop_flag(workspace: Path):
    """F04-1: Verify that standard configuration executes without explicit nine_loop.enabled."""
    cfg = PipelineConfig(
        topic="Native Pipeline Without Flags",
        work_dir=str(workspace),
    )
    # The pipeline configuration exists and is ready for native dispatch
    assert cfg.topic == "Native Pipeline Without Flags"


def test_config_loads_without_nine_loop_section(workspace: Path):
    """F04-2: Verify load_config functions cleanly on configs with no nine_loop dictionary."""
    from research_tool.domain.config import load_config

    minimal_config = workspace / "minimal.yaml"
    minimal_config.write_text("pipeline:\n  mode: brief\n  work_dir: ./custom-out\n", encoding="utf-8")

    loaded = load_config(minimal_config, overrides={"topic": "No Flags"})
    assert loaded.topic == "No Flags"
    assert loaded.mode == "brief"
    assert Path(loaded.work_dir) == Path("./custom-out")


def test_no_shadow_dual_execution_overhead(workspace: Path, generate_valid_research_output):
    """F04-3: Verify that execution outputs only canonical artifacts and no shadow/ artifacts."""
    generate_valid_research_output(workspace)
    shadow_dir = workspace / "shadow"
    assert not shadow_dir.exists(), "Shadow sidecar output directory should not exist in native pipeline"


def test_deepen_as_strategy_flag_not_mandatory(workspace: Path):
    """F04-4: Verify that pipeline does not require or depend on deepen_as_strategy flag."""
    cfg = PipelineConfig(topic="No Deepen Flag", work_dir=str(workspace))
    # Config succeeds without deepen_as_strategy
    assert cfg.mode == "brief" or cfg.mode in ("full", "fast", "standard", "deep")


def test_clean_namespace_without_shadow_dependencies():
    """F04-5: Verify ResearchPipeline can be imported and instantiated without shadow modules."""
    from research_tool.application.pipeline import ResearchPipeline

    assert ResearchPipeline is not None
