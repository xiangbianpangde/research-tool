"""A fresh run with actually new source observations can satisfy explicit novelty review."""

import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher


@pytest.mark.asyncio
@pytest.mark.parametrize("genuine_new_source", [False, True])
async def test_source_novelty_checks_body_and_literal_not_run_or_claim_id(
    corpus, monkeypatch, genuine_new_source
):
    reg, issue, summary, graph = imported(corpus)
    old = graph["facts"][0]["text"]
    quote = (
        "A new independent experiment retained 97 percent capacity after 2100 cycles."
        if genuine_new_source
        else old
    )

    async def fetch(self, url):
        return FetchResult(
            "https://papers.test/new-release",
            quote + "\n\nNewly released study methods and limitations. " * 7,
            ok=True,
        )

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    from research_tool.infrastructure.search.base import SearchHit

    class Backend:
        async def search(self, *args, **kwargs):
            return [
                SearchHit(
                    url="https://papers.test/new-release",
                    title="New experiment",
                    source_engine="web",
                )
            ]

    monkeypatch.setattr(
        "research_tool.infrastructure.stages.collector.get_backend", lambda *args: Backend()
    )

    def reply(prompt, schema):
        if schema.__name__ == "_ChunkResult":
            return schema(claims=[{"text": quote, "quote": quote, "topic": "new-topic-changes-ID"}])
        return model_reply(prompt, schema)

    cfg = config(corpus[1].parent, minimum=2)
    cfg.resume = False
    cfg.nine_loop.max_rounds = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered")
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=reply)).run()
    s = json.loads(result.run_summary_path.read_text())
    new_graph = json.loads(
        (result.report_result.report_path.parent / "tree/knowledge.json").read_text()
    )
    ref = reg.claim_reference("corpus", s["run_id"], new_graph["facts"][0]["claim_id"])
    assert ref["run_id"] != summary["run_id"] and ref["claim_id"] != graph["facts"][0]["claim_id"]
    scope = {
        k: (
            "scientific_gap"
            if k == "purpose"
            else issue["scope"][k]
            if k == "target_requirement"
            else "experiment"
        )
        for k in issue["scope"]
    }
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Fixed atomic scope",
            "kind": "scientific_gap",
            "scope": scope,
            "evidence_refs": [ref],
        },
        "c",
    )
    payload = {
        "rationale": "Review novelty and exact study acceptance separately",
        "evidence_refs": [ref],
        "acceptance_met": True,
        "acceptance_evidence": [{"criterion": scope["target_requirement"], "evidence_ref": ref}],
        "scientific_answered": True,
        "new_scientific_evidence_added": True,
    }
    if genuine_new_source:
        decide(reg, issue, "resolved", payload, "new")
        assert reg.view()["counts"]["new_scientific_evidence_added"] == 1
    else:
        from research_tool.infrastructure.disposition import DispositionError

        with pytest.raises(DispositionError, match="new-science"):
            decide(reg, issue, "resolved", payload, "duplicate-body")
