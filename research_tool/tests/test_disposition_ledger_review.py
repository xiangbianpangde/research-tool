"""Ledger review regressions: request/payload mismatch and rejected stale network recovery."""

import copy
import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.records import digest
from research_tool.infrastructure.disposition.schema import validate_event
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_stale_network_can_be_rejected_without_being_adopted(corpus):
    reg, _, summary, graph = imported(corpus)
    network, policy = mapping(reg, summary, graph)
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="p", actor="agent"
    )
    path = corpus[3] / "evidence-audit.json"
    path.write_bytes(path.read_bytes() + b" ")
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=False,
        reason="Evidence stale, reject instead of applying",
        expected_head=reg.head(),
        idempotency_key="reject",
        actor="agent",
    )
    assert reg.view()["network"] is None and reg.view()["freshness"] == "stale"


@pytest.mark.asyncio
async def test_request_and_applied_proposal_are_bound_even_after_rehash(corpus):
    reg, issue, _, _ = imported(corpus)
    reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Original assessment", "assessment": "unassessed"},
        expected_head=reg.head(),
        idempotency_key="p",
        actor="agent",
    )
    _, _, events = reg._read()
    event = copy.deepcopy(events[-1])
    event["request"]["operation"] = "out_of_scope"
    event["request_sha256"] = digest(
        {"type": event["type"], "payload": event["request"], "actor": event["actor_label"]}
    )
    with pytest.raises(DispositionError, match="request"):
        validate_event(event)
