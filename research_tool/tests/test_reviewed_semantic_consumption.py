"""Reviewed semantic mappings must affect the actual report body, not only issue totals."""

import json
import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import DispositionConfig, ReporterConfig, RetrievalConfig
from research_tool.infrastructure.disposition import Registry
from research_tool.infrastructure.disposition.records import digest
from research_tool.infrastructure.disposition.network import PURPOSE_RULES
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401
from research_tool.tests.test_researchctl_grounded_contract import module_root, client
from research_tool.tests.test_grounded_pipeline_synthesis import grounded_reply


@pytest.mark.asyncio
async def test_actual_grounded_report_consumes_exact_reviewed_role_scope_and_versions(
    tmp_path, external_io
):
    cfg = config(tmp_path / "origin")
    cfg.reporter = ReporterConfig(evidence_policy="layered")
    origin = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(origin.run_summary_path.read_text())
    final = origin.report_result.report_path.parent
    graph = json.loads((final / "tree/knowledge.json").read_text())
    reg = Registry.create(
        tmp_path / "registry",
        registry_id="semantic-report",
        roots={"origin": str(origin.topic_dir)},
    )
    ref = reg.claim_reference("origin", summary["run_id"], graph["facts"][0]["claim_id"])
    scope = {
        "purpose": "descriptive",
        "population": "fixture experiments",
        "challenge": "battery cycling",
        "intervention_condition": "reported experiment only",
        "outcome": "retained capacity",
        "time_window": "1000 cycles",
        "denominator_unit": "experiments, not patients",
        "target_requirement": "Study-bound observation, no individual risk",
    }
    network = {
        "schema": "network-evidence-map/v1",
        "evidence_refs": [ref],
        "assignments": [
            {
                "claim_id": ref["claim_id"],
                "concept_id": "retained-capacity-observation",
                "role": "primary_result",
                "scope": scope,
            }
        ],
        "studies": [],
        "edges": [],
    }
    policy = {
        "schema": "assessment-policy/v1",
        "policy_id": "fixture-policy",
        "version": 1,
        "purposes": PURPOSE_RULES,
    }
    p = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=True,
        reason="Exact source scope reviewed",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    cfg.work_dir = tmp_path / "reviewed"
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.reporter = ReporterConfig(evidence_policy="layered", grounding_policy="source_grounded")
    seen = []

    def reply(prompt, schema):
        if schema.__name__ in {"ObservationChoice", "AtomChecks"}:
            seen.append((schema.__name__, prompt))
        return grounded_reply(prompt, schema)

    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=reply)).run()
    assert result.verdict == "STOP_SUCCESS", result
    report = result.report_result.report_path.read_text()
    assert "retained-capacity-observation" in report and "experiments, not patients" in report
    assert "primary_result" in report and "reported experiment only" in report
    assert all("REVIEWED_SEMANTICS:" in prompt for _, prompt in seen)
    assert all("retained-capacity-observation" in prompt for _, prompt in seen)
    verifier_prompt = next(prompt for kind, prompt in seen if kind == "AtomChecks")
    atoms = json.loads(verifier_prompt.split("\nATOMS:", 1)[1].split("\nCOMPLETE_SOURCE", 1)[0])
    assert all(
        a["primary_evidence"] and a["primary_evidence"][0]["claim_id"] in a["claim_ids"]
        for a in atoms
    )
    audit = json.loads(
        (result.report_result.report_path.parent / "grounding-audit.json").read_text()
    )
    assert audit["reviewed_semantics"]["network_sha256"] == digest(network)
    assert audit["reviewed_semantics"]["issue_head_sha256"] == reg.head()
    assert client(result.topic_dir).index()["status"] == "success"
