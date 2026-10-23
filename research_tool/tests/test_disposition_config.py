"""YAML reviewed policy must not be silently dropped while SDK policy works."""

from pathlib import Path
import pytest
from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError


def test_yaml_disposition_section_roundtrips(tmp_path):
    path = tmp_path / "reviewed.yaml"
    path.write_text(
        'disposition:\n  policy: reviewed\n  registry_root: /review/issues\n  expected_head_sha256: "'
        + "a" * 64
        + '"\n'
    )
    result = load_config(path)
    assert result.disposition.policy == "reviewed"
    assert result.disposition.registry_root == Path("/review/issues")


def test_malformed_disposition_section_is_rejected(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text("disposition: ignored-is-not-safe\n")
    with pytest.raises(ConfigValidationError, match="disposition"):
        load_config(path)
