"""verify_flags unit tests — single-source OK / multi-source loud FAIL."""
from __future__ import annotations

import pytest

from research_tool.nine_loop.flags import FlagsFault, verify_flags


def test_verify_flags_single_source_ok():
    """Product package has exactly one FLAG_DEFAULTS source (nine_loop.flags)."""
    res = verify_flags()
    assert res["count"] == 1
    assert res["sources"] == ["research_tool.nine_loop.flags"]


def test_verify_flags_multi_source_loud_fail(tmp_path):
    """A competing FLAG_DEFAULTS definition → loud FlagsFault (E_VERSION)."""
    (tmp_path / "research_tool").mkdir()
    (tmp_path / "research_tool" / "nine_loop").mkdir()
    (tmp_path / "research_tool" / "nine_loop" / "flags.py").write_text(
        "FLAG_DEFAULTS: dict = {}\n")
    (tmp_path / "research_tool" / "rogue_flags.py").write_text(
        "FLAG_DEFAULTS = {}\n")
    (tmp_path / "research_tool" / "__init__.py").write_text("")
    (tmp_path / "research_tool" / "nine_loop" / "__init__.py").write_text("")
    with pytest.raises(FlagsFault) as ei:
        verify_flags(package_root=tmp_path / "research_tool")
    assert "multi-source" in str(ei.value.safe_message)
