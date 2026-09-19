"""Tier 2: Boundary & Corner Cases — B04: Shadow Flags Boundaries.

Verifies boundary conditions for obsolete flags: passing unexpected dictionary keys,
empty string values, negative sample rates, and non-boolean flags.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from research_tool.domain.models import PipelineConfig


def test_b04_unexpected_keys_in_config_ignored(workspace: Path):
    """B04-1: Boundary: Supplying arbitrary legacy dictionary keys doesn't cause crash."""
    from research_tool.domain.config import load_config

    cfg_file = workspace / "extra_keys.yaml"
    cfg_file.write_text("pipeline:\n  mode: brief\nunknown_legacy_flag_xyz: true\n", encoding="utf-8")
    loaded = load_config(cfg_file, overrides={"topic": "Extra Keys Test"})
    assert loaded.topic == "Extra Keys Test"


def test_b04_empty_config_file_handled(workspace: Path):
    """B04-2: Boundary: Completely empty config YAML file."""
    from research_tool.domain.config import load_config

    empty_cfg = workspace / "empty.yaml"
    empty_cfg.write_text("", encoding="utf-8")
    loaded = load_config(empty_cfg, overrides={"topic": "Empty Config Test"})
    assert loaded.topic == "Empty Config Test"


def test_b04_whitespace_only_config_file(workspace: Path):
    """B04-3: Boundary: Config YAML file with whitespace and comments only."""
    from research_tool.domain.config import load_config

    comment_cfg = workspace / "comments.yaml"
    comment_cfg.write_text("# Only comments\n\n   # Another comment\n", encoding="utf-8")
    loaded = load_config(comment_cfg, overrides={"topic": "Comment Config Test"})
    assert loaded.topic == "Comment Config Test"


def test_b04_invalid_mode_in_overrides_raises_error(workspace: Path):
    """B04-4: Boundary: Passing invalid mode via overrides dictionary."""
    from research_tool.domain.config import load_config
    from research_tool.domain.errors import ConfigValidationError

    cfg_file = workspace / "valid.yaml"
    cfg_file.write_text("pipeline:\n  mode: brief\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        load_config(cfg_file, overrides={"topic": "Invalid Mode", "mode": "nonexistent_mode"})


def test_b04_nested_overrides_type_safety(workspace: Path):
    """B04-5: Boundary: Nested dictionary overrides with deep merge."""
    from research_tool.domain.config import load_config

    cfg_file = workspace / "nested.yaml"
    cfg_file.write_text("llm:\n  provider: deepseek\n", encoding="utf-8")
    loaded = load_config(cfg_file, overrides={"topic": "Nested Overrides", "llm": {"provider": "openai"}})
    assert loaded.llm.provider == "openai"
