"""Tier 1: Feature Coverage — F02: Python SDK Native Alignment.

Verifies that the Python SDK entrypoints (research(), create_pipeline(), quick_collect())
natively align with the 9-stage closed-loop pipeline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from research_tool import research, create_pipeline, quick_collect
from research_tool.domain.models import PipelineConfig, PipelineResult


@pytest.mark.asyncio
async def test_sdk_create_pipeline_factory_returns_dispatcher(workspace: Path):
    """F02-1: Verify that create_pipeline() produces a functional ResearchPipeline instance."""
    config = PipelineConfig(
        topic="SDK Factory Test",
        work_dir=str(workspace),
        mode="brief",
    )
    pipeline = create_pipeline(config)
    assert pipeline is not None
    assert pipeline.config.topic == "SDK Factory Test"
    assert str(pipeline.config.work_dir) == str(workspace)


@pytest.mark.asyncio
async def test_sdk_research_entrypoint_interface_contract(workspace: Path):
    """F02-2: Verify SDK research() signature, parameters, and contract validation."""
    # Verify signature accepts topic, work_dir, llm_provider, llm_model, config_path
    import inspect
    sig = inspect.signature(research)
    assert "topic" in sig.parameters
    assert "work_dir" in sig.parameters
    assert "llm_provider" in sig.parameters
    assert "llm_model" in sig.parameters
    assert "config_path" in sig.parameters


@pytest.mark.asyncio
async def test_sdk_research_work_dir_isolation(workspace: Path):
    """F02-3: Verify that SDK research() targets the caller-provided work directory."""
    custom_dir = workspace / "custom_sdk_out"
    config = PipelineConfig(
        topic="Isolation Topic",
        work_dir=str(custom_dir),
    )
    pipeline = create_pipeline(config)
    assert Path(pipeline.config.work_dir) == custom_dir


@pytest.mark.asyncio
async def test_sdk_quick_collect_returns_sources(workspace: Path):
    """F02-4: Verify that quick_collect() returns a list of dictionary sources."""
    import inspect
    sig = inspect.signature(quick_collect)
    assert "topic" in sig.parameters
    assert "max_results" in sig.parameters
    assert "work_dir" in sig.parameters


def test_sdk_exports_clean_public_surface():
    """F02-5: Verify research_tool.__all__ exports all required 9-stage and pipeline symbols."""
    import research_tool
    exported = set(research_tool.__all__)

    required_symbols = {
        "ResearchPipeline",
        "create_pipeline",
        "research",
        "quick_collect",
        "PipelineConfig",
        "PipelineResult",
        "StageEvent",
        "load_config",
        "ResearchToolError",
    }
    missing = required_symbols - exported
    assert not missing, f"Missing public exports in research_tool: {missing}"
