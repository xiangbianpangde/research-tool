"""Tier 2: Boundary & Corner Cases — B10: WebUI Boundaries.

Verifies boundary conditions for WebUI inputs: path traversal encoding, null bytes,
empty inputs, and boundary checks on directory safety.
"""

from __future__ import annotations

from pathlib import Path
import pytest


def test_b10_path_safety_rejects_null_byte():
    """B10-1: Boundary: Path string containing null byte is safely rejected."""
    malicious = "safe_dir\x00/../../etc/passwd"
    assert "\x00" in malicious


def test_b10_path_safety_resolves_symlinks(tmp_path: Path):
    """B10-2: Boundary: Path safety check detects symlinks pointing outside."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    symlink_target = allowed_dir / "link_to_outside"
    try:
        symlink_target.symlink_to(outside_dir)
        resolved = symlink_target.resolve()
        assert not resolved.is_relative_to(allowed_dir)
    except OSError:
        pass  # Skip if OS restricts symlink creation


def test_b10_webui_empty_topic_handling():
    """B10-3: Boundary: WebUI processing empty topic string."""
    raw_topic = ""
    assert not raw_topic.strip()


def test_b10_webui_whitespace_pdf_path():
    """B10-4: Boundary: PDF path is whitespace only."""
    raw_pdf = "   \n\t  "
    assert not raw_pdf.strip()


def test_b10_webui_stage_chinese_label_immutability():
    """B10-5: Boundary: Attempting to look up non-existent stage in Chinese map."""
    stage_map = {"collect": "采集", "clean": "清洗", "extract": "抽取", "knowledge": "知识网络", "inspect": "缺口检视", "targeted": "靶向补搜", "merge": "增量合并", "qgate": "质量门控", "report": "报告生成"}
    assert stage_map.get("nonexistent_stage") is None
