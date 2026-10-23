"""A10: metadata disposition must not waive an uncorrected required raw finding."""

import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide


@pytest.mark.asyncio
async def test_constraint_reclassification_does_not_silently_waive_raw_required_finding(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Reviewed as a standing contextual requirement.",
            "evidence_refs": [ref],
            "kind": "persistent_constraint",
            "scope": {**issue["scope"], "purpose": "persistent_constraint"},
        },
        "classify",
    )
    decide(
        reg,
        issue,
        "reclassified_constraint",
        {
            "rationale": "Preserve explicit constraint, do not waive raw finding.",
            "evidence_refs": [ref],
        },
        "constraint",
    )
    origin = next(o for o in reg.view()["observations"].values() if o["kind"] == "finding")
    evaluated = evaluate_registry(reg.root, reg.head(), {"findings": [origin["original"]]}, graph)
    assert evaluated["required_unresolved_total"] >= len(evaluated["active_required_issues"]) + 1
    assert evaluated["reviewed_unresolved_findings"] == [origin["original"]]
    assert evaluated["constraints"]
