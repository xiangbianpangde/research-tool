"""Nonclosing history metadata cannot revalidate or replace closure evidence."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_history_metadata_does_not_change_disposition_evidence_or_clear_stale(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    original_decision = issue["last_decision"]
    issue = decide(
        reg,
        issue,
        "link_history",
        {
            "rationale": "Nonclosing history",
            "relation": "related_to",
            "target_ref": ref,
            "evidence_refs": [ref],
        },
        "history",
    )
    assert reg.view()["issues"][issue["issue_id"]]["last_decision"] == original_decision
    issue = decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Exact closure evidence", "evidence_refs": [ref]},
        "fix",
    )
    network, policy = mapping(reg, summary, graph)
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="Map changed",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    current = reg.view()["issues"][issue["issue_id"]]
    assert current["needs_revalidation"]
    with pytest.raises(DispositionError):
        decide(
            reg,
            current,
            "link_history",
            {
                "rationale": "Cannot use metadata to renew closure",
                "relation": "related_to",
                "target_ref": ref,
                "evidence_refs": [ref],
            },
            "launder",
        )
