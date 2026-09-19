"""Tier 2: Boundary & Corner Cases — B02: Python SDK Boundaries.

Verifies boundary conditions for SDK entrypoints: empty topic, whitespace topic,
non-existent config paths, and invalid provider strings.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from research_tool.domain.models import PipelineConfig
from research_tool.domain.errors import ConfigValidationError
from research_tool import create_pipeline


def test_b02_empty_topic_string(workspace: Path):
    """B02-1: Boundary: PipelineConfig with empty topic."""
    cfg = PipelineConfig(topic="", work_dir=str(workspace))
    assert cfg.topic == ""


def test_b02_whitespace_only_topic_string(workspace: Path):
    """B02-2: Boundary: PipelineConfig with whitespace-only topic."""
    cfg = PipelineConfig(topic="   \t\n  ", work_dir=str(workspace))
    assert cfg.topic.strip() == ""


def test_b02_nonexistent_config_path_raises_error(workspace: Path):
    """B02-3: Boundary: load_config with non-existent path raises ConfigValidationError."""
    from research_tool.domain.config import load_config

    missing_path = workspace / "nonexistent_config_123.yaml"
    with pytest.raises(ConfigValidationError):
        load_config(missing_path)


def test_b02_custom_deep_work_dir_path(workspace: Path):
    """B02-4: Boundary: Deeply nested target work directory."""
    deep_path = workspace / "sub1" / "sub2" / "sub3" / "target"
    cfg = PipelineConfig(topic="Deep Path Topic", work_dir=str(deep_path))
    pipeline = create_pipeline(cfg)
    assert Path(pipeline.config.work_dir) == deep_path


def test_b02_invalid_mode_defaults_rejection():
    """B02-5: Boundary: Requesting defaults for an empty or invalid mode."""
    from research_tool.domain.config import mode_defaults

    with pytest.raises(ConfigValidationError):
        mode_defaults("")
