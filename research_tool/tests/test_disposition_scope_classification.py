"""Unclassified raw alerts are not permission to close a clinical question as wording."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide


@pytest.mark.asyncio
async def test_unclassified_original_cannot_be_closed_with_any_unrelated_claim(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="corrected",
            expected_issue_revision=issue["revision"],
            payload={
                "rationale": "An exact quote exists, but the question is not yet classified.",
                "evidence_refs": [ref],
            },
            expected_head=reg.head(),
            idempotency_key="bad",
            actor="agent",
        )


@pytest.mark.asyncio
async def test_scoped_classification_keeps_original_and_constraint_separate(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    classified = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Review only the original missing wording, not future risk.",
            "evidence_refs": [ref],
            "kind": "report_wording",
            "scope": {**issue["scope"], "purpose": "report_wording"},
        },
        "classify",
    )
    assert classified["required"] is True
    assert classified["scope"]["purpose"] == "report_wording"
    assert classified["origin_ids"] == issue["origin_ids"]
    fixed = decide(
        reg,
        classified,
        "corrected",
        {"rationale": "Original source supports this wording.", "evidence_refs": [ref]},
        "fix",
    )
    assert fixed["lifecycle"] == "closed" and fixed["scientific_answered"] is False
