"""Independent review regressions: hidden closures, linked priority and replay context."""

import copy
import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.infrastructure.disposition.state import apply_event
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_omitted_finding_does_not_skip_closed_decision_current_graph_validation(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Existing exact wording checked", "evidence_refs": [ref]},
        "fix",
    )
    changed = copy.deepcopy(graph)
    changed["facts"] = []
    with pytest.raises(DispositionError, match="current"):
        evaluate_registry(reg.root, reg.head(), {"findings": []}, changed)


@pytest.mark.asyncio
async def test_replay_boundary_rejects_approve_after_network_context_changed(corpus):
    reg, issue, summary, graph = imported(corpus)
    p = reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Old context", "assessment": "partially_answered"},
        expected_head=reg.head(),
        idempotency_key="p",
        actor="agent",
    )
    _, state, _ = reg._read()
    state["network"] = {"intervening": "reviewed-network"}
    proposal = state["proposals"][p["proposal_id"]]
    event = {
        "type": "review",
        "payload": {
            "proposal_id": p["proposal_id"],
            "proposal_sha256": p["proposal_sha256"],
            "context_sha256": proposal["context_sha256"],
            "approve": True,
        },
    }
    with pytest.raises(DispositionError, match="context"):
        apply_event(state, event)
