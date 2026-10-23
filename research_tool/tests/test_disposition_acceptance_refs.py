"""Every acceptance evidence reference is validated, not just scientific-gap payloads."""

import sys
import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.state import validate_disposition, scope_for
from research_tool.infrastructure.disposition.records import digest, encoded, byte_hash
from research_tool.tests.test_researchctl_grounded_contract import module_root


def test_nonscientific_acceptance_dangling_reference_is_rejected():
    issue = {
        "issue_id": "a",
        "kind": "report_wording",
        "scope": {**scope_for("Exact criterion"), "purpose": "report_wording"},
        "lifecycle": "working",
        "assessment": "unassessed",
        "required": True,
    }
    payload = {
        "rationale": "Cannot bypass acceptance references",
        "evidence_refs": [{"claim_id": "valid"}],
        "acceptance_met": True,
        "acceptance_evidence": [
            {"criterion": "Exact criterion", "evidence_ref": {"claim_id": "dangling"}}
        ],
    }
    with pytest.raises(DispositionError, match="acceptance"):
        validate_disposition({"issues": {"a": issue}}, issue, "resolved", payload)
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_rules import transition
    from researchctl.layouts.research_tool import LayoutError

    with pytest.raises(LayoutError, match="acceptance"):
        transition({"issues": {"a": issue}}, issue, "resolved", payload)


def test_reader_rejects_noncanonical_array_pointer_tokens():
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evidence import reference_value
    from researchctl.layouts.research_tool import LayoutError

    data = encoded({"facts": [{"value": 1}]})
    pin = byte_hash(data)
    for pointer in ["/facts/-1", "/facts/+0", "/facts/00", "/facts/1", "/facts/~2"]:
        ref = {
            "root_id": "r",
            "run_id": "a" * 32,
            "path": "audit.json",
            "artifact_sha256": pin,
            "run_record_sha256": "b" * 64,
            "json_pointer": pointer,
            "value_sha256": digest({"value": 1}),
        }
        with pytest.raises(LayoutError):
            reference_value({pin: data.decode()}, ref)
