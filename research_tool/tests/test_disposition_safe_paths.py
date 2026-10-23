"""A13 exact whitelist/no-follow boundaries: no arbitrary file or source path is evidence."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.records import (
    safe_bytes,
    validate_reference,
    strict_json,
    pointer,
)


def test_symlink_ancestor_and_leaf_traversal_and_duplicate_json_are_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "data.json").write_text("{}")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(DispositionError):
        safe_bytes(alias, "data.json")
    (actual / "link.json").symlink_to(actual / "data.json")
    with pytest.raises(DispositionError):
        safe_bytes(actual, "link.json")
    for relative in ["../actual/data.json", "/etc/passwd", "a/../../data.json"]:
        with pytest.raises(DispositionError):
            safe_bytes(actual, relative)
    for raw in [b'{"schema":1,"schema":2}', b'{"value":NaN}']:
        with pytest.raises(DispositionError):
            strict_json(raw)
    with pytest.raises(DispositionError):
        pointer([1, 2], "/01")


def test_unregistered_root_is_rejected_before_read(tmp_path):
    ref = {
        "root_id": "outside",
        "run_id": "a" * 32,
        "path": "runs/" + "a" * 32 + "/final/tree/knowledge.json",
        "artifact_sha256": "a" * 64,
        "run_record_sha256": "b" * 64,
        "claim_id": "c",
    }
    with pytest.raises(DispositionError, match="unregistered"):
        validate_reference({"allowed": str(tmp_path)}, ref)
