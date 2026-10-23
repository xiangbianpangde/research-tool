"""A02/A05/A09/A10/A13: children and unresolved data cannot disappear behind dispositions."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


def imported(corpus):
    import hashlib

    reg, topic, summary, final, audit, graph = corpus
    reg.import_audit(
        root_id="corpus",
        run_id=summary["run_id"],
        expected_audit_sha256=hashlib.sha256(
            (final / "evidence-audit.json").read_bytes()
        ).hexdigest(),
        expected_head=reg.head(),
        idempotency_key="import",
        actor="agent",
    )
    return reg, next(iter(reg.view()["issues"].values())), summary, graph


def classify_wording(reg, issue, ref, key="classify-wording"):
    if issue["kind"] != "unclassified":
        return issue
    return decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Classify this exact imported wording task; no scientific conclusion.",
            "kind": "report_wording",
            "scope": {**issue["scope"], "purpose": "report_wording"},
            "evidence_refs": [ref],
        },
        key,
    )


def decide(reg, issue, op, payload, key):
    p = reg.propose(
        issue["issue_id"],
        operation=op,
        expected_issue_revision=issue["revision"],
        payload=payload,
        expected_head=reg.head(),
        idempotency_key=key,
        actor="agent",
    )
    reg.review(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="Exact source and atomic requirement reviewed.",
        expected_head=reg.head(),
        idempotency_key=key + "-review",
        actor="agent",
    )
    return reg.view()["issues"][issue["issue_id"]]


@pytest.mark.asyncio
async def test_decomposed_parent_does_not_hide_open_children_or_change_their_question(corpus):
    reg, issue, summary, graph = imported(corpus)
    question = issue["scope"]["target_requirement"]
    mid = len(question) // 2
    parts = [
        {"scope": {**issue["scope"], "target_requirement": part}, "coverage_text": part}
        for part in [question[:mid], question[mid:]]
    ]
    decide(
        reg,
        issue,
        "decomposed",
        {
            "rationale": "Partition original compound requirement, no scientific resolution",
            "parts": parts,
        },
        "split",
    )
    state = reg.view()
    assert state["counts"]["required_unresolved"] >= 2
    parent = state["issues"][issue["issue_id"]]
    assert parent["resolution"] == "decomposed"
    assert all(state["issues"][cid]["lifecycle"] == "open" for cid in parent["children"])


@pytest.mark.asyncio
async def test_waiting_data_cannot_resolve_and_required_cannot_be_reclassified_away(corpus):
    reg, issue, summary, graph = imported(corpus)
    issue = decide(
        reg,
        issue,
        "assess",
        {"rationale": "No complete event denominator exists", "assessment": "awaiting_data"},
        "wait",
    )
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    for op in ["resolved", "out_of_scope", "reclassified_constraint"]:
        with pytest.raises(DispositionError):
            reg.propose(
                issue["issue_id"],
                operation=op,
                expected_issue_revision=issue["revision"],
                payload={
                    "rationale": "Cannot hide missing scientific data",
                    "evidence_refs": [ref],
                    "acceptance_met": True,
                },
                expected_head=reg.head(),
                idempotency_key=op,
                actor="agent",
            )


@pytest.mark.asyncio
async def test_review_cannot_claim_human_authority_or_change_exact_proposal(corpus):
    reg, issue, summary, graph = imported(corpus)
    p = reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Limited evidence", "assessment": "partially_answered"},
        expected_head=reg.head(),
        idempotency_key="partial",
        actor="agent",
    )
    with pytest.raises(DispositionError):
        reg.review(
            p["proposal_id"],
            expected_proposal_sha256=p["proposal_sha256"],
            approve=True,
            reason="Not an authenticated human",
            expected_head=reg.head(),
            idempotency_key="human",
            actor="agent",
            authority="human_clinical",
        )
    assert reg.view()["issues"][issue["issue_id"]]["lifecycle"] == "pending_review"
