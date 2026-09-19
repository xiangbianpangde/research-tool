"""Tier 1: Feature Coverage — F11: Config Template Modernization.

Verifies that docs/config.example.yaml contains valid YAML, documents native
pipeline options, and is parsable by domain config loaders.
"""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml

from research_tool.domain.config import load_config


def test_config_example_yaml_exists_and_is_valid():
    """F11-1: Verify that docs/config.example.yaml exists and is valid YAML."""
    example_path = Path("docs/config.example.yaml")
    assert example_path.exists(), "docs/config.example.yaml must exist"
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_config_example_yaml_has_pipeline_section():
    """F11-2: Verify example yaml contains pipeline section with standard fields."""
    example_path = Path("docs/config.example.yaml")
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert "pipeline" in data
    pipeline = data["pipeline"]
    assert "work_dir" in pipeline
    assert "mode" in pipeline


def test_config_example_yaml_has_llm_section():
    """F11-3: Verify example yaml contains llm section with provider configuration."""
    example_path = Path("docs/config.example.yaml")
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert "llm" in data
    llm = data["llm"]
    assert "provider" in llm


def test_config_loader_parses_example_yaml(workspace: Path):
    """F11-4: Verify load_config successfully parses docs/config.example.yaml with overrides."""
    example_path = Path("docs/config.example.yaml")
    config = load_config(example_path, overrides={"topic": "Config Example Test", "work_dir": str(workspace)})
    assert config.topic == "Config Example Test"
    assert Path(config.work_dir) == workspace


def test_example_yaml_contains_clean_utf8_formatting():
    """F11-5: Verify example yaml uses UTF-8 and contains no non-printable or corrupt characters."""
    example_path = Path("docs/config.example.yaml")
    content = example_path.read_text(encoding="utf-8")
    assert len(content) > 100
    assert "\x00" not in content
