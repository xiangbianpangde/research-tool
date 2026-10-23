"""Reviewed duplicates retain an active canonical target and follow its genuine resolution."""

import hashlib
import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.mark.asyncio
async def test_duplicate_current_finding_counts_once_and_canonical_can_resolve(corpus):
    reg, first, summary, graph = imported(corpus)
    cfg = config(corpus[1].parent, minimum=2)
    cfg.resume = False
    cfg.nine_loop.max_rounds = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered")
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    newer = json.loads(result.run_summary_path.read_text())
    audit = result.report_result.report_path.parent / "evidence-audit.json"
    reg.import_audit(
        root_id="corpus",
        run_id=newer["run_id"],
        expected_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),
        expected_head=reg.head(),
        idempotency_key="second",
        actor="agent",
    )
    original = reg.view()["observations"][first["origin_ids"][0]]["original"]
    second = next(
        x
        for x in reg.view()["issues"].values()
        if x["issue_id"] != first["issue_id"]
        and reg.view()["observations"][x["origin_ids"][0]]["original"] == original
    )
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    for n, item in enumerate([first, second]):
        scope = {
            k: (
                "representation"
                if k == "purpose"
                else item["scope"][k]
                if k == "target_requirement"
                else "fixed atomic scope"
            )
            for k in item["scope"]
        }
        updated = decide(
            reg,
            item,
            "classify",
            {
                "rationale": "Same scoped representation task explicitly checked",
                "scope": scope,
                "kind": "representation",
                "evidence_refs": [ref],
            },
            f"c{n}",
        )
        if n == 0:
            first = updated
        else:
            second = updated
    second = decide(
        reg,
        second,
        "duplicate",
        {
            "rationale": "Exact atomic acceptance and scope identical",
            "target_issue_id": first["issue_id"],
            "evidence_refs": [ref],
        },
        "dup",
    )
    evaluation = evaluate_registry(reg.root, reg.head(), {"findings": [original]}, graph)
    assert evaluation["required_unresolved_total"] == reg.view()["counts"]["required_unresolved"]
    first = decide(
        reg,
        first,
        "corrected",
        {"rationale": "Canonical representation corrected", "evidence_refs": [ref]},
        "canonical",
    )
    evaluation = evaluate_registry(reg.root, reg.head(), {"findings": [original]}, graph)
    assert evaluation["reviewed_unresolved_findings"] == []
    assert original["finding_id"] in evaluation["corrected_original_ids"]
