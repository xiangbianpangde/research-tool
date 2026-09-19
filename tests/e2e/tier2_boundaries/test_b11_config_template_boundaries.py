"""Tier 2: Boundary & Corner Cases — B11: Config Template Boundaries.

Verifies boundary conditions for configuration templates: missing optional fields,
corrupted YAML syntax, unicode characters, and type coercions.
"""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError


def test_b11_malformed_yaml_syntax_raises_error(workspace: Path):
    """B11-1: Boundary: Config file with invalid YAML syntax."""
    bad_yaml = workspace / "bad_syntax.yaml"
    bad_yaml.write_text("pipeline:\n  mode: [unclosed list\n", encoding="utf-8")

    with pytest.raises(Exception):
        load_config(bad_yaml)


def test_b11_yaml_with_boolean_coercions(workspace: Path):
    """B11-2: Boundary: YAML boolean representations (yes, no, true, false)."""
    bool_yaml = workspace / "bool.yaml"
    bool_yaml.write_text("pipeline:\n  mode: brief\n  resume: yes\n", encoding="utf-8")
    loaded = load_config(bool_yaml, overrides={"topic": "Bool Topic"})
    assert loaded.resume is True


def test_b11_yaml_with_utf8_chinese_characters(workspace: Path):
    """B11-3: Boundary: Config YAML containing Chinese comments and values."""
    cn_yaml = workspace / "cn.yaml"
    cn_yaml.write_text("# 中文注释\npipeline:\n  mode: brief\n", encoding="utf-8")
    loaded = load_config(cn_yaml, overrides={"topic": "量子计算调研"})
    assert loaded.topic == "量子计算调研"


def test_b11_yaml_with_empty_dictionaries(workspace: Path):
    """B11-4: Boundary: Config YAML with empty subsection blocks."""
    empty_sub_yaml = workspace / "empty_sub.yaml"
    empty_sub_yaml.write_text("pipeline:\n  mode: brief\nllm:\ncollector:\n", encoding="utf-8")
    loaded = load_config(empty_sub_yaml, overrides={"topic": "Empty Subs"})
    assert loaded.topic == "Empty Subs"


def test_b11_yaml_with_numeric_string_values(workspace: Path):
    """B11-5: Boundary: YAML string representing numbers."""
    num_yaml = workspace / "num.yaml"
    num_yaml.write_text("pipeline:\n  mode: 'brief'\n", encoding="utf-8")
    loaded = load_config(num_yaml, overrides={"topic": "12345"})
    assert loaded.topic == "12345"
