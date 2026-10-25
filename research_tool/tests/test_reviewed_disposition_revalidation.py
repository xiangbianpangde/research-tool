"""A new reviewed network needs explicit, evidence-identical revalidation, not hidden closure reuse."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.records import digest
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, classify_wording, decide
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_researchctl_grounded_contract import module_root


@pytest.mark.asyncio
async def test_explicit_review_revalidates_same_disposition_without_changing_children_or_evidence(
    corpus,
):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    issue = decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Exact original representation corrected", "evidence_refs": [ref]},
        "fix",
    )
    original_decision = issue["last_decision"]
    network, policy = mapping(reg, summary, graph)
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="New map reviewed",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    stale = reg.view()["issues"][issue["issue_id"]]
    assert stale["needs_revalidation"] and stale["effective_lifecycle"] == "open"
    payload = {
        "rationale": "Recheck identical closure against new reviewed map; no scope or science change",
        "prior_decision_sha256": digest(original_decision),
        "evidence_refs": [ref],
    }
    renewed = decide(reg, stale, "revalidate", payload, "rv")
    assert renewed["last_decision"] == original_decision
    assert renewed["resolution"] == "corrected" and renewed["effective_lifecycle"] == "closed"
    assert renewed["needs_revalidation"] is False and renewed["scientific_answered"] is False
    from research_tool.infrastructure.disposition.evaluation import evaluate_registry

    value = evaluate_registry(reg.root, reg.head(), {"findings": []}, graph)
    import sys

    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evaluation import verify

    verify(value, graph)


@pytest.mark.asyncio
async def test_revalidation_cannot_replace_original_evidence_or_decision_identity(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    issue = decide(
        reg, issue, "corrected", {"rationale": "Original evidence", "evidence_refs": [ref]}, "fix"
    )
    for payload in [
        {
            "rationale": "Wrong prior decision",
            "prior_decision_sha256": "0" * 64,
            "evidence_refs": [ref],
        },
        {
            "rationale": "Omit source support",
            "prior_decision_sha256": digest(issue["last_decision"]),
            "evidence_refs": [],
        },
    ]:
        with pytest.raises(DispositionError):
            reg.propose(
                issue["issue_id"],
                operation="revalidate",
                expected_issue_revision=issue["revision"],
                payload=payload,
                expected_head=reg.head(),
                idempotency_key=payload["rationale"],
                actor="agent",
            )
