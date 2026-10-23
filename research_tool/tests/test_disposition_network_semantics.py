"""Purpose checks and source-context mappings need reviewable exact support."""

import copy
import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.network import validate_network, assess_network
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_disposition_transitions import imported


@pytest.mark.asyncio
async def test_context_of_requires_real_span_containment_not_just_same_source(corpus):
    reg, _, summary, graph = imported(corpus)
    network, policy = mapping(reg, summary, graph)
    # Distinct claims in the same paper are not automatically context containment.
    if len(graph["facts"]) == 1:
        # Use a real second fact from its own committed producer run below.
        from research_tool.application.pipeline import ResearchPipeline
        from research_tool.infrastructure.llm import MockLLMClient
        from research_tool.tests.test_nine_loop_runner import config, model_reply

        def both(prompt, schema):
            if schema.__name__ == "_ChunkResult":
                return schema(
                    claims=[
                        {"text": text, "quote": text, "topic": "experiment"}
                        for text in [
                            graph["facts"][0]["text"],
                            "Experimental methods and limitations.",
                        ]
                    ]
                )
            return model_reply(prompt, schema)

        cfg = config(corpus[1].parent)
        cfg.resume = False
        result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=both)).run()
        import json

        summary = json.loads(result.run_summary_path.read_text())
        graph = json.loads(
            (result.report_result.report_path.parent / "tree/knowledge.json").read_text()
        )
        network, policy = mapping(reg, summary, graph)
    left, right = graph["facts"][:2]
    network["edges"] = [
        {
            "kind": "context_of",
            "from_claim_id": left["claim_id"],
            "to_claim_id": right["claim_id"],
            "evidence_claim_ids": [left["claim_id"], right["claim_id"]],
            "scope": network["assignments"][0]["scope"],
        }
    ]
    with pytest.raises(DispositionError, match="contain"):
        validate_network(network, policy, reg.view()["roots"])


@pytest.mark.asyncio
async def test_purpose_assessment_cannot_infer_sufficiency_from_grouping(corpus):
    reg, _, summary, graph = imported(corpus)
    network, policy = mapping(reg, summary, graph)
    for a in network["assignments"]:
        a["scope"]["purpose"] = "cross_study"
    report = assess_network(validate_network(network, policy, reg.view()["roots"]), policy)
    group = report["groups"][0]
    assert group["assessment_status"] == "needs_review"
    assert "independence" in group["unmet_requirements"]
    assert group["independent_cohort_count"] is None
    assert report["new_scientific_evidence_added"] == 0


@pytest.mark.asyncio
async def test_reviewed_metadata_assessment_is_not_scientific_progress(corpus):
    reg, _, summary, graph = imported(corpus)
    network, policy = mapping(reg, summary, graph)
    assignment = network["assignments"][0]
    from research_tool.infrastructure.disposition.records import digest

    network["assessments"] = [
        {
            "concept_id": assignment["concept_id"],
            "scope_sha256": digest(assignment["scope"]),
            "checks": [
                {
                    "requirement": "original_identity",
                    "status": "met",
                    "evidence_claim_ids": [assignment["claim_id"]],
                    "reason": "Original study identity checked in exact source.",
                }
            ],
        }
    ]
    report = assess_network(validate_network(network, policy, reg.view()["roots"]), policy)
    assert report["groups"][0]["assessment_status"] == "requirements_met"
    assert report["groups"][0]["scientific_sufficiency"] == "not_inferred"
    assert report["automatic_issue_closures"] == 0
    bad = copy.deepcopy(network)
    bad["assessments"][0]["checks"][0]["evidence_claim_ids"] = ["uncommitted"]
    with pytest.raises(DispositionError):
        validate_network(bad, policy, reg.view()["roots"])
