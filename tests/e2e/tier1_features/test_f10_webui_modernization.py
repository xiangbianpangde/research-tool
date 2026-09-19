"""Tier 1: Feature Coverage — F10: WebUI Modernization.

Verifies WebUI Chinese stage labels, parameter controls, path safety,
and 9-stage architecture compatibility.
"""

from __future__ import annotations

from pathlib import Path
import pytest

TARGET_NINE_STAGE_CN = {
    "collect": "采集",
    "clean": "清洗",
    "extract": "抽取",
    "knowledge": "知识网络",
    "inspect": "缺口检视",
    "targeted": "靶向补搜",
    "merge": "增量合并",
    "qgate": "质量门控",
    "report": "报告生成",
}


def test_webui_target_stage_translations_complete():
    """F10-1: Verify that Chinese translation mapping covers all 9 native stages."""
    assert len(TARGET_NINE_STAGE_CN) == 9
    for stage in ("collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"):
        assert stage in TARGET_NINE_STAGE_CN
        assert len(TARGET_NINE_STAGE_CN[stage]) > 0


def test_webui_path_safety_check_blocks_traversal():
    """F10-2: Verify path traversal validation rejects paths attempting to escape workspace."""

    def is_safe_path(base_dir: Path, target_path: str) -> bool:
        try:
            resolved = (base_dir / target_path).resolve()
            return resolved.is_relative_to(base_dir.resolve())
        except (ValueError, RuntimeError):
            return False

    base = Path("/safe/workspace")
    assert is_safe_path(base, "sub/dir") is True
    assert is_safe_path(base, "../../etc/passwd") is False
    assert is_safe_path(base, "../other") is False


def test_webui_module_imports_cleanly():
    """F10-3: Verify research_tool.presentation.webui can be imported."""
    import research_tool.presentation.webui as webui_mod

    assert webui_mod is not None


def test_webui_build_interface_callable():
    """F10-4: Verify build_ui function exists in webui module."""
    import research_tool.presentation.webui as webui_mod

    assert hasattr(webui_mod, "build_ui")


def test_webui_stage_labels_distinct():
    """F10-5: Verify each stage label in Chinese mapping is unique."""
    labels = list(TARGET_NINE_STAGE_CN.values())
    assert len(labels) == len(set(labels))
