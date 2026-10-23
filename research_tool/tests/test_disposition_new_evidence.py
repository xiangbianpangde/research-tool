"""Scientific resolution requires classified scope, exact acceptance evidence and review."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide


@pytest.mark.asyncio
async def test_scientific_answer_is_a_reviewed_scope_decision_not_a_closure_counter(
    corpus, tmp_path
):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    scope = {
        **issue["scope"],
        "purpose": "scientific_gap",
        "population": "fixture adults",
        "challenge": "battery experiment",
        "intervention_condition": "as reported",
        "outcome": "cycle life",
        "time_window": "measured study period",
        "denominator_unit": "experiments",
    }
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Atomic experiment question with fixed acceptance.",
            "kind": "scientific_gap",
            "scope": scope,
            "evidence_refs": [ref],
        },
        "classify",
    )
    payload = {
        "rationale": "Original exact experiment supports this limited study observation, not general risk.",
        "evidence_refs": [ref],
        "acceptance_met": True,
        "acceptance_evidence": [{"criterion": scope["target_requirement"], "evidence_ref": ref}],
        "scientific_answered": True,
        "new_scientific_evidence_added": False,
    }
    result = decide(reg, issue, "resolved", payload, "resolve")
    assert result["lifecycle"] == "closed" and result["scientific_answered"] is True
    assert reg.view()["counts"]["scientifically_answered"] == 1
    assert reg.view()["counts"]["new_scientific_evidence_added"] == 0


@pytest.mark.asyncio
async def test_new_run_same_source_literal_is_not_new_scientific_evidence(corpus, tmp_path):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    scope = {
        **issue["scope"],
        "purpose": "scientific_gap",
        "population": "fixture adults",
        "challenge": "experiment",
        "intervention_condition": "as reported",
        "outcome": "cycle life",
        "time_window": "reported",
        "denominator_unit": "experiments",
    }
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Scope fixed.",
            "kind": "scientific_gap",
            "scope": scope,
            "evidence_refs": [ref],
        },
        "classify",
    )
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="resolved",
            expected_issue_revision=issue["revision"],
            payload={
                "rationale": "Cannot claim novelty for the same source span.",
                "evidence_refs": [ref],
                "acceptance_met": True,
                "acceptance_evidence": [
                    {"criterion": scope["target_requirement"], "evidence_ref": ref}
                ],
                "scientific_answered": True,
                "new_scientific_evidence_added": True,
            },
            expected_head=reg.head(),
            idempotency_key="false-novelty",
            actor="agent",
        )
