"""Reviewed dependencies must be committed as exact same-body facts before any gate adopts them."""

import pytest
from research_tool.infrastructure.disposition.reporting import reviewed_dependencies
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, classify_wording, decide
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_reviewed_existing_claim_can_be_reintroduced_only_for_same_current_body(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Exact source literal already supports correction", "evidence_refs": [ref]},
        "close",
    )
    view = reg.view()
    current = {**graph, "facts": []}
    delta = reviewed_dependencies(view, current)
    assert delta["facts"] == graph["facts"]
    assert delta["sources"] == graph["sources"]
    assert delta["new_scientific_evidence_added"] == 0
    current["sources"] = [{**graph["sources"][0], "content_sha256": "0" * 64}]
    with pytest.raises(ValueError, match="source"):
        reviewed_dependencies(view, current)
