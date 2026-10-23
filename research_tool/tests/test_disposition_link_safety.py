"""A09/A10: lexical labels and unknown scopes cannot consolidate distinct requirements."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.state import validate_disposition


def issue(i, **kw):
    from research_tool.infrastructure.disposition.state import scope_for

    return {
        "issue_id": i,
        "scope": scope_for("Independent source coverage is insufficient."),
        "kind": "unclassified",
        "required": True,
        "lifecycle": "open",
        "children": [],
        "links": [],
        "assessment": "unassessed",
        **kw,
    }


def test_unknown_scopes_and_same_wording_are_not_duplicate_proof():
    a, b = issue("a"), issue("b")
    with pytest.raises(DispositionError):
        validate_disposition(
            {"issues": {"a": a, "b": b}},
            a,
            "duplicate",
            {"rationale": "Same words are insufficient", "target_issue_id": "b"},
        )


def test_scientific_gap_cannot_be_reclassified_to_wording_after_reopen():
    a = issue("a", kind="scientific_gap", classification_reopened=True)
    a["scope"]["purpose"] = "scientific_gap"
    with pytest.raises(DispositionError):
        validate_disposition(
            {"issues": {"a": a}},
            a,
            "classify",
            {
                "rationale": "Cannot erase the scientific requirement",
                "kind": "report_wording",
                "scope": {**a["scope"], "purpose": "report_wording"},
                "evidence_refs": [{"claim_id": "e"}],
            },
        )


def test_pending_review_target_cannot_bypass_closed_target_restriction():
    a, b = issue("a"), issue("b", lifecycle="pending_review", lifecycle_before_proposal="closed")
    for x in [a, b]:
        x["scope"] = {
            k: ("persistent_constraint" if k == "purpose" else "fixed") for k in x["scope"]
        }
        x["kind"] = "persistent_constraint"
    with pytest.raises(DispositionError):
        validate_disposition(
            {"issues": {"a": a, "b": b}},
            a,
            "duplicate",
            {
                "rationale": "Pending reopen is not active approval",
                "target_issue_id": "b",
                "evidence_refs": [{"claim_id": "e"}],
            },
        )


def test_link_cycle_is_rejected_with_complete_matching_scope():
    a, b = issue("a"), issue("b")
    for x in [a, b]:
        x["scope"] = {k: ("scientific_gap" if k == "purpose" else "fixed") for k in x["scope"]}
        x["kind"] = "scientific_gap"
    b["links"] = [{"kind": "duplicate", "target": "a"}]
    with pytest.raises(DispositionError, match="cycle"):
        validate_disposition(
            {"issues": {"a": a, "b": b}},
            a,
            "duplicate",
            {
                "rationale": "Cycle forbidden",
                "target_issue_id": "b",
                "evidence_refs": [{"claim_id": "e"}],
            },
        )
