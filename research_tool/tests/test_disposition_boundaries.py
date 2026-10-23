"""Review decisions must not erase a question by changing its meaning or payload shape."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording


@pytest.mark.asyncio
async def test_decomposition_cannot_rewrite_intervention_or_drop_requiredness(corpus):
    reg, issue, summary, graph = imported(corpus)
    text = issue["scope"]["target_requirement"]
    mid = len(text) // 2
    parts = [
        {
            "scope": {
                **issue["scope"],
                "target_requirement": part,
                "intervention_condition": "all prophylaxis absent",
            },
            "coverage_text": part,
        }
        for part in [text[:mid], text[mid:]]
    ]
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="decomposed",
            expected_issue_revision=issue["revision"],
            payload={"rationale": "Cannot widen original clinical conditions", "parts": parts},
            expected_head=reg.head(),
            idempotency_key="widen",
            actor="agent",
        )


@pytest.mark.asyncio
async def test_unknown_payload_cannot_smuggle_scope_or_scientific_result(corpus):
    reg, issue, summary, graph = imported(corpus)
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="assess",
            expected_issue_revision=issue["revision"],
            payload={
                "rationale": "No scope override",
                "assessment": "partially_answered",
                "required": False,
            },
            expected_head=reg.head(),
            idempotency_key="hidden",
            actor="agent",
        )


@pytest.mark.asyncio
async def test_rejected_review_can_be_reproposed_and_closed_issue_requires_reopen(corpus):
    reg, issue, summary, graph = imported(corpus)
    p = reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Evidence partial", "assessment": "partially_answered"},
        expected_head=reg.head(),
        idempotency_key="partial",
        actor="agent",
    )
    reg.review(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=False,
        reason="More evidence needed",
        expected_head=reg.head(),
        idempotency_key="reject",
        actor="agent",
    )
    assert reg.view()["issues"][issue["issue_id"]]["lifecycle"] == "working"
    current = reg.view()["issues"][issue["issue_id"]]
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    current = classify_wording(reg, current, ref)
    current = decide(
        reg,
        current,
        "corrected",
        {"rationale": "Existing wording evidence checked", "evidence_refs": [ref]},
        "fix",
    )
    with pytest.raises(DispositionError):
        reg.propose(
            current["issue_id"],
            operation="assess",
            expected_issue_revision=current["revision"],
            payload={"rationale": "Must reopen first", "assessment": "unassessed"},
            expected_head=reg.head(),
            idempotency_key="bad",
            actor="agent",
        )
    current = decide(
        reg, current, "reopen", {"rationale": "New counterevidence needs review"}, "reopen"
    )
    assert current["lifecycle"] == "working" and current["resolution"] is None
