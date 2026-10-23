"""Scope/network/policy drift cannot inherit a prior approval or hide unresolved children."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported
from research_tool.tests.test_disposition_network import mapping


@pytest.mark.asyncio
async def test_pending_issue_review_is_invalidated_by_new_network_policy(corpus):
    reg, issue, summary, graph = imported(corpus)
    proposal = reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Evidence partial", "assessment": "partially_answered"},
        expected_head=reg.head(),
        idempotency_key="p",
        actor="agent",
    )
    network, policy = mapping(reg, summary, graph)
    np = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        np["proposal_id"],
        expected_proposal_sha256=np["proposal_sha256"],
        approve=True,
        reason="New reviewed source map",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    with pytest.raises(DispositionError):
        reg.review(
            proposal["proposal_id"],
            expected_proposal_sha256=proposal["proposal_sha256"],
            approve=True,
            reason="Old mapping cannot approve",
            expected_head=reg.head(),
            idempotency_key="stale",
            actor="agent",
        )
    assert reg.view()["issues"][issue["issue_id"]]["lifecycle"] == "pending_review"


@pytest.mark.asyncio
async def test_idempotency_key_rejects_changed_payload_even_with_old_expected_head(corpus):
    reg, issue, summary, graph = imported(corpus)
    head = reg.head()
    payload = {"rationale": "Need data", "assessment": "awaiting_data"}
    reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload=payload,
        expected_head=head,
        idempotency_key="same",
        actor="agent",
    )
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="assess",
            expected_issue_revision=issue["revision"],
            payload={**payload, "assessment": "unassessed"},
            expected_head=head,
            idempotency_key="same",
            actor="agent",
        )
