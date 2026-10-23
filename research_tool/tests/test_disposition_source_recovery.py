"""Broken historical evidence stays unadoptable but must not prevent metadata-only reopen."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide, classify_wording


@pytest.mark.asyncio
async def test_stale_source_allows_reopen_not_adoption_and_exact_restore_recovers(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = classify_wording(reg, issue, ref)
    issue = decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Exact wording corrected", "evidence_refs": [ref]},
        "fix",
    )
    path = corpus[3] / "evidence-audit.json"
    before = path.read_bytes()
    path.write_bytes(before + b" ")
    assert reg.view()["freshness"] == "stale"
    issue = decide(
        reg,
        issue,
        "reopen",
        {"rationale": "Source corruption requires restoration and review"},
        "reopen",
    )
    assert issue["lifecycle"] == "working" and issue["needs_revalidation"]
    with pytest.raises(DispositionError):
        reg.snapshot(expected_head=reg.head())
    path.write_bytes(before)
    assert reg.snapshot(expected_head=reg.head())["freshness"] == "fresh"
    assert reg.view()["issues"][issue["issue_id"]]["resolution"] is None
