"""Producer state and independent read-only reader replay agree across real reviewed events."""

import sys
import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_reader_replays_real_review_history_network_reopen_constraint_and_children(corpus):
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evaluation import verify

    reg, issue, summary, graph = imported(corpus)

    def check():
        value = evaluate_registry(reg.root, reg.head(), {"findings": []}, graph)
        verify(value, graph)

    check()
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    issue = decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Exact representation wording", "evidence_refs": [ref]},
        "fix",
    )
    check()
    network, policy = mapping(reg, summary, graph)
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="Review map",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    check()
    issue = decide(
        reg,
        reg.view()["issues"][issue["issue_id"]],
        "reopen",
        {"rationale": "New map needs recheck"},
        "reopen",
    )
    check()
    text = issue["scope"]["target_requirement"]
    parts = [
        {"scope": {**issue["scope"], "target_requirement": t}, "coverage_text": t}
        for t in [text[: len(text) // 2], text[len(text) // 2 :]]
    ]
    decide(
        reg,
        issue,
        "decomposed",
        {"rationale": "All requirement clauses covered", "parts": parts},
        "split",
    )
    check()
    limitation = next(
        i
        for i in reg.view()["issues"].values()
        if isinstance(reg.view()["observations"][i["origin_ids"][0]]["original"], str)
    )
    limitation = decide(
        reg,
        limitation,
        "classify",
        {
            "rationale": "Fixed inference constraint",
            "kind": "persistent_constraint",
            "scope": {**limitation["scope"], "purpose": "persistent_constraint"},
            "evidence_refs": [ref],
        },
        "lc",
    )
    limitation = decide(
        reg,
        limitation,
        "reclassified_constraint",
        {"rationale": "Keep inference boundary", "evidence_refs": [ref]},
        "constraint",
    )
    check()
    limitation = decide(
        reg, limitation, "reopen", {"rationale": "Constraint requires renewed review"}, "lr"
    )
    decide(
        reg,
        limitation,
        "link_history",
        {
            "rationale": "Exact origin history",
            "relation": "related_to",
            "target_ref": ref,
            "evidence_refs": [ref],
        },
        "history",
    )
    check()
