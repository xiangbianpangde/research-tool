"""Linked requirements, new limits and hard failures must survive reviewed consumption."""

import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry, reviewed_gate
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide


def test_hard_failure_is_never_overridden_by_reviewed_or_layered():
    for verdict in ["STOP_ERROR", "STOP_BUDGET", "STOP_CANCELLED"]:
        decision = reviewed_gate(
            {"verdict": verdict},
            {
                "required_unresolved_high": 0,
                "required_unresolved_total": 0,
                "issue_head_sha256": "0" * 64,
            },
            max_high=0,
            max_total=0,
            evidence_policy="layered",
            final_round=True,
        )
        assert decision["verdict"] == verdict


@pytest.mark.asyncio
async def test_new_unregistered_limitation_is_an_additional_floor(corpus):
    reg, _, _, graph = imported(corpus)
    e = evaluate_registry(
        reg.root,
        reg.head(),
        {"findings": []},
        graph,
        known_limitations=["A new required future-risk limitation."],
    )
    assert e["required_unresolved_total"] == reg.view()["counts"]["required_unresolved"] + 1
    assert e["unmatched_current_limitations"] == ["A new required future-risk limitation."]


@pytest.mark.asyncio
async def test_decomposed_parent_is_handled_when_all_children_are_reviewed(corpus):
    from research_tool.tests.test_disposition_transitions import classify_wording

    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    original = reg.view()["observations"][issue["origin_ids"][0]]["original"]
    text = issue["scope"]["target_requirement"]
    parts = [
        {"coverage_text": t, "scope": {**issue["scope"], "target_requirement": t}}
        for t in [text[: len(text) // 2], text[len(text) // 2 :]]
    ]
    parent = decide(
        reg,
        issue,
        "decomposed",
        {"rationale": "All original clauses covered", "parts": parts},
        "split",
    )
    for n, cid in enumerate(parent["children"]):
        child = classify_wording(reg, reg.view()["issues"][cid], ref, f"classify-{n}")
        decide(
            reg,
            child,
            "corrected",
            {"rationale": "Existing exact wording corrected only", "evidence_refs": [ref]},
            f"fix-{n}",
        )
    e = evaluate_registry(reg.root, reg.head(), {"findings": [original]}, graph)
    assert e["reviewed_unresolved_findings"] == []
    assert original["finding_id"] in e["corrected_original_ids"]
