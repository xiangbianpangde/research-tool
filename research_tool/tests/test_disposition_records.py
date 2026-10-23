"""A01-A05: exact origin identity and existing-evidence corrections, not mutable text truth."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.disposition import Registry, DispositionError
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.fixture
async def corpus(tmp_path, external_io):
    cfg = config(tmp_path / "evidence", minimum=2)
    cfg.nine_loop.max_rounds = 0
    cfg.reporter = ReporterConfig(
        evidence_policy="layered",
        known_limitations=["Finite follow-up cannot prove lifetime safety."],
    )
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_SUCCESS"
    topic = result.topic_dir
    summary = json.loads(result.run_summary_path.read_text())
    final = result.report_result.report_path.parent
    audit = json.loads((final / "evidence-audit.json").read_text())
    graph = json.loads((final / "tree/knowledge.json").read_text())
    registry = Registry.create(
        tmp_path / "issues", registry_id="fixture-registry", roots={"corpus": str(topic)}
    )
    return registry, topic, summary, final, audit, graph


def test_origin_identity_uses_artifact_pointer_and_run_not_model_id():
    from research_tool.infrastructure.disposition.records import occurrence_id

    base = {
        "root_id": "corpus",
        "run_id": "1" * 32,
        "path": "runs/" + "1" * 32 + "/final/evidence-audit.json",
        "artifact_sha256": "a" * 64,
        "json_pointer": "/unresolved_findings/0",
        "value_sha256": "b" * 64,
        "run_record_sha256": "c" * 64,
    }
    assert occurrence_id(base) == occurrence_id(copy.deepcopy(base))
    for key, value in [
        ("run_id", "2" * 32),
        ("artifact_sha256", "d" * 64),
        ("json_pointer", "/known_limitations/0"),
    ]:
        assert occurrence_id({**base, key: value}) != occurrence_id(base)


@pytest.mark.asyncio
async def test_actual_producer_observations_are_imported_losslessly_and_idempotently(corpus):
    reg, topic, summary, final, audit, graph = corpus
    original = {
        str(p.relative_to(topic)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in topic.rglob("*")
        if p.is_file()
    }
    head = reg.head()
    receipt = reg.import_audit(
        root_id="corpus",
        run_id=summary["run_id"],
        expected_audit_sha256=hashlib.sha256(
            (final / "evidence-audit.json").read_bytes()
        ).hexdigest(),
        expected_head=head,
        idempotency_key="import-1",
        actor="test-agent",
    )
    view = reg.view()
    assert len(view["observations"]) == len(audit["unresolved_findings"]) + 1
    assert view["counts"]["scientifically_answered"] == 0
    assert view["counts"]["closed"] == 0
    assert [
        o["original"] for o in view["observations"].values() if o["kind"] == "limitation"
    ] == audit["known_limitations"]
    assert (
        reg.import_audit(
            root_id="corpus",
            run_id=summary["run_id"],
            expected_audit_sha256=hashlib.sha256(
                (final / "evidence-audit.json").read_bytes()
            ).hexdigest(),
            expected_head=head,
            idempotency_key="import-1",
            actor="test-agent",
        )
        == receipt
    )
    assert original == {
        str(p.relative_to(topic)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in topic.rglob("*")
        if p.is_file()
    }
    audit_path = final / "evidence-audit.json"
    audit_path.write_bytes(audit_path.read_bytes() + b" ")
    assert reg.view()["freshness"] == "stale"
    with pytest.raises(DispositionError):
        reg.snapshot(expected_head=reg.head(), require_fresh=True)


@pytest.mark.asyncio
async def test_proposal_is_not_a_close_and_review_is_pinned_to_existing_evidence(corpus):
    reg, topic, summary, final, audit, graph = corpus
    reg.import_audit(
        root_id="corpus",
        run_id=summary["run_id"],
        expected_audit_sha256=hashlib.sha256(
            (final / "evidence-audit.json").read_bytes()
        ).hexdigest(),
        expected_head=reg.head(),
        idempotency_key="import",
        actor="agent",
    )
    issue = next(iter(reg.view()["issues"].values()))
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    from research_tool.tests.test_disposition_transitions import classify_wording

    issue = classify_wording(reg, issue, ref)
    proposal = reg.propose(
        issue["issue_id"],
        operation="corrected",
        expected_issue_revision=issue["revision"],
        payload={
            "rationale": "Original evidence already answers this wording task.",
            "evidence_refs": [ref],
            "scientific_answered": False,
            "new_scientific_evidence_added": False,
        },
        expected_head=reg.head(),
        idempotency_key="p1",
        actor="agent",
    )
    pending = reg.view()["issues"][issue["issue_id"]]
    assert pending["lifecycle"] == "pending_review" and reg.view()["counts"]["closed"] == 0
    with pytest.raises(DispositionError):
        reg.review(
            proposal["proposal_id"],
            expected_proposal_sha256="0" * 64,
            approve=True,
            reason="Wrong proposal must fail",
            expected_head=reg.head(),
            idempotency_key="bad-review",
            actor="agent",
        )
    reg.review(
        proposal["proposal_id"],
        expected_proposal_sha256=proposal["proposal_sha256"],
        approve=True,
        reason="Source wording and exact scope checked.",
        expected_head=reg.head(),
        idempotency_key="review",
        actor="agent",
    )
    fixed = reg.view()["issues"][issue["issue_id"]]
    assert fixed["resolution"] == "corrected" and fixed["lifecycle"] == "closed"
    assert fixed["scientific_answered"] is False
    assert reg.view()["counts"]["new_scientific_evidence_added"] == 0
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="resolved",
            expected_issue_revision=fixed["revision"],
            payload={"rationale": "No evidence", "evidence_refs": []},
            expected_head=reg.head(),
            idempotency_key="no-evidence",
            actor="agent",
        )
