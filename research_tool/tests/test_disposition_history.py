"""A14: a reviewed history link never rewrites the source execution or review."""

import hashlib
import pytest
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide


@pytest.mark.asyncio
async def test_reviewed_history_link_is_nonclosing_and_preserves_old_run_bytes(corpus):
    reg, issue, summary, graph = imported(corpus)
    origin = reg.view()["observations"][issue["origin_ids"][0]]["origin"]
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in corpus[1].rglob("*")
        if p.is_file()
    }
    decide(
        reg,
        issue,
        "link_history",
        {
            "rationale": "Preserve exact historical observations without changing verdict",
            "relation": "related_to",
            "target_ref": origin,
            "evidence_refs": [origin],
        },
        "history",
    )
    view = reg.view()
    assert len(view["history_links"]) == 1
    assert view["counts"]["closed"] == 0 and view["counts"]["scientifically_answered"] == 0
    assert next(iter(view["history_links"].values()))["target_ref"] == origin
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in corpus[1].rglob("*")
        if p.is_file()
    }
