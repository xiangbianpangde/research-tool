"""Adversarial acceptance: compatibility, event replay, fresh evidence and recovery."""

import json

import pytest

from research_tool.application.pipeline import ResearchPipeline, _bytes
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.records import byte_hash, encoded
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_network import mapping
from research_tool.tests.test_disposition_transitions import imported, decide
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.mark.asyncio
async def test_default_raw_keeps_pre_disposition_runtime19_identity(tmp_path, external_io):
    cfg = config(tmp_path)
    legacy = cfg.model_dump(mode="json", exclude={"resume", "work_dir", "disposition", "retrieval"})
    for key in ("api_key", "session_id"):
        legacy["llm"].pop(key, None)
    for key in ("tavily_api_key", "semantic_scholar_api_key", "github_token", "proxy"):
        legacy["collector"].pop(key, None)
    expected = byte_hash(
        _bytes({"runtime_contract": 19, "topic": cfg.topic, "config": legacy, "inputs": {}})
    )
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert json.loads(result.run_summary_path.read_text())["input_fingerprint"] == expected


@pytest.mark.asyncio
async def test_stale_pending_proposal_can_be_rejected_and_reproposed(corpus):
    reg, issue, summary, graph = imported(corpus)
    p = reg.propose(
        issue["issue_id"],
        operation="assess",
        expected_issue_revision=issue["revision"],
        payload={"rationale": "Need original context", "assessment": "partially_answered"},
        expected_head=reg.head(),
        idempotency_key="pending",
        actor="agent",
    )
    network, policy = mapping(reg, summary, graph)
    n = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        n["proposal_id"],
        expected_proposal_sha256=n["proposal_sha256"],
        approve=True,
        reason="Updated mapping reviewed",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    reg.review(
        p["proposal_id"],
        expected_proposal_sha256=p["proposal_sha256"],
        approve=False,
        reason="Superseded context, not an approval",
        expected_head=reg.head(),
        idempotency_key="reject",
        actor="agent",
    )
    current = reg.view()["issues"][issue["issue_id"]]
    assert current["pending_proposal_id"] is None
    assert (
        decide(
            reg,
            current,
            "assess",
            {"rationale": "Recheck new map", "assessment": "awaiting_data"},
            "new",
        )["assessment"]
        == "awaiting_data"
    )


@pytest.mark.asyncio
async def test_replay_rejects_unknown_revision_fields_even_if_rehashed(corpus):
    reg = corpus[0]
    revision = json.loads((reg.root / "revisions" / (reg.head() + ".json")).read_bytes())
    revision["unreviewed_scientific_answer"] = True
    data = encoded(revision)
    pin = byte_hash(data)
    (reg.root / "revisions" / (pin + ".json")).write_bytes(data)
    (reg.root / "HEAD.json").write_bytes(
        encoded({"schema": "issue-head/v1", "revision_sha256": pin})
    )
    with pytest.raises(DispositionError):
        reg.view()


@pytest.mark.asyncio
async def test_scientific_answer_counts_exclude_stale_effective_decisions(corpus):
    reg, issue, summary, graph = imported(corpus)
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    scope = {
        **issue["scope"],
        **dict.fromkeys(
            [
                "population",
                "challenge",
                "intervention_condition",
                "outcome",
                "time_window",
                "denominator_unit",
            ],
            "fixture",
        ),
        "purpose": "scientific_gap",
    }
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Atomic fixed scope",
            "kind": "scientific_gap",
            "scope": scope,
            "evidence_refs": [ref],
        },
        "c",
    )
    decide(
        reg,
        issue,
        "resolved",
        {
            "rationale": "Limited reviewed observation",
            "acceptance_met": True,
            "acceptance_evidence": [
                {"criterion": scope["target_requirement"], "evidence_ref": ref}
            ],
            "evidence_refs": [ref],
            "scientific_answered": True,
        },
        "r",
    )
    network, policy = mapping(reg, summary, graph)
    n = reg.propose_network(
        network, policy, expected_head=reg.head(), idempotency_key="np", actor="agent"
    )
    reg.review_network(
        n["proposal_id"],
        expected_proposal_sha256=n["proposal_sha256"],
        approve=True,
        reason="New map",
        expected_head=reg.head(),
        idempotency_key="nr",
        actor="agent",
    )
    assert reg.view()["counts"]["scientifically_answered"] == 0


@pytest.mark.asyncio
async def test_new_run_same_source_cannot_be_relabelled_novel(corpus):
    reg, issue, summary, graph = imported(corpus)
    cfg = config(corpus[1].parent, minimum=2)
    cfg.nine_loop.max_rounds = 0
    cfg.resume = False
    cfg.reporter = ReporterConfig(
        evidence_policy="layered",
        known_limitations=["Finite follow-up cannot prove lifetime safety."],
    )
    rerun = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    after = json.loads(rerun.run_summary_path.read_text())
    assert after["run_id"] != summary["run_id"]
    ref = reg.claim_reference("corpus", after["run_id"], graph["facts"][0]["claim_id"])
    scope = {
        **issue["scope"],
        **dict.fromkeys(
            [
                "population",
                "challenge",
                "intervention_condition",
                "outcome",
                "time_window",
                "denominator_unit",
            ],
            "fixture",
        ),
        "purpose": "scientific_gap",
    }
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Atomic fixed scope",
            "kind": "scientific_gap",
            "scope": scope,
            "evidence_refs": [ref],
        },
        "c",
    )
    with pytest.raises(DispositionError):
        reg.propose(
            issue["issue_id"],
            operation="resolved",
            expected_issue_revision=issue["revision"],
            payload={
                "rationale": "Same full body is not new evidence",
                "acceptance_met": True,
                "acceptance_evidence": [
                    {"criterion": scope["target_requirement"], "evidence_ref": ref}
                ],
                "evidence_refs": [ref],
                "scientific_answered": True,
                "new_scientific_evidence_added": True,
            },
            expected_head=reg.head(),
            idempotency_key="novel",
            actor="agent",
        )


@pytest.mark.asyncio
async def test_reopening_constraint_supersedes_its_active_projection(corpus):
    reg, _, summary, graph = imported(corpus)
    issue = next(
        x
        for x in reg.view()["issues"].values()
        if isinstance(reg.view()["observations"][x["origin_ids"][0]]["original"], str)
    )
    ref = reg.claim_reference("corpus", summary["run_id"], graph["facts"][0]["claim_id"])
    issue = decide(
        reg,
        issue,
        "classify",
        {
            "rationale": "Persistent inference boundary",
            "kind": "persistent_constraint",
            "scope": {**issue["scope"], "purpose": "persistent_constraint"},
            "evidence_refs": [ref],
        },
        "c",
    )
    issue = decide(
        reg,
        issue,
        "reclassified_constraint",
        {"rationale": "Keep boundary visible", "evidence_refs": [ref]},
        "constraint",
    )
    assert reg.view()["counts"]["constraints"] == 1
    decide(reg, issue, "reopen", {"rationale": "Need new review"}, "reopen")
    assert reg.view()["counts"]["constraints"] == 0
    assert all(c["applicability"] == "superseded" for c in reg.view()["constraints"].values())
