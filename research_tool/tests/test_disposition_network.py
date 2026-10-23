"""A06/A07/A11: graph organization is not independent scientific evidence."""

import copy
import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.network import validate_network, assess_network
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording


def mapping(reg, summary, graph):
    refs = [reg.claim_reference("corpus", summary["run_id"], f["claim_id"]) for f in graph["facts"]]
    scope = {
        "purpose": "study_metadata",
        "population": "original cohort",
        "challenge": "unknown",
        "intervention_condition": "unknown",
        "outcome": "unknown",
        "time_window": "study period",
        "denominator_unit": "unknown",
        "target_requirement": "study statement identity",
    }
    network = {
        "schema": "network-evidence-map/v1",
        "evidence_refs": refs,
        "assignments": [
            {
                "claim_id": f["claim_id"],
                "concept_id": "study-design",
                "role": "primary_result",
                "scope": scope,
            }
            for f in graph["facts"]
        ],
        "studies": [
            {
                "source_id": s["source_id"],
                "study_id": "study-" + s["source_id"],
                "cohort_id": "shared-registry",
                "evidence_claim_ids": [
                    f["claim_id"]
                    for f in graph["facts"]
                    if f["citations"][0]["source_id"] == s["source_id"]
                ],
            }
            for s in graph["sources"]
        ],
        "edges": [],
    }
    policy = {
        "schema": "assessment-policy/v1",
        "policy_id": "purpose-aware",
        "version": 1,
        "purposes": {
            "study_metadata": "identity_only",
            "descriptive": "scope_required",
            "cross_study": "independence_and_design_required",
        },
    }
    return network, policy


@pytest.mark.asyncio
async def test_network_rejects_unpinned_model_relations_and_does_not_close_findings(corpus):
    reg, issue, summary, graph = imported(corpus)
    network, policy = mapping(reg, summary, graph)
    valid = validate_network(network, policy, reg.view()["roots"])
    report = assess_network(valid, policy)
    assert report["new_scientific_evidence_added"] == 0
    assert report["automatic_issue_closures"] == 0
    assert report["groups"][0]["rule"] == "identity_only"
    assert report["groups"][0]["scientific_sufficiency"] == "not_assessed"
    bad = copy.deepcopy(network)
    bad["edges"] = [
        {"kind": "causes", "subject": "mutation", "object": "bleeding", "confidence": 1}
    ]
    with pytest.raises(DispositionError):
        validate_network(bad, policy, reg.view()["roots"])


@pytest.mark.asyncio
async def test_network_review_context_changes_make_old_decisions_require_revalidation(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    decide(
        reg, issue, "corrected", {"rationale": "Wording checked", "evidence_refs": [ref]}, "correct"
    )
    network, policy = mapping(reg, summary, graph)
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="network-p", actor="agent"
    )
    before = reg.view()
    assert before["network"] is None
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="Exact source identities and mapping scope reviewed.",
        expected_head=reg.head(),
        idempotency_key="network-r",
        actor="agent",
    )
    view = reg.view()
    assert view["network"]["schema"] == "network-evidence-map/v1"
    changed = view["issues"][issue["issue_id"]]
    assert changed["effective_lifecycle"] == "open"
    assert changed["needs_revalidation"] is True
    assert view["counts"]["required_unresolved"] >= 1
    assert view["counts"]["scientifically_answered"] == 0
