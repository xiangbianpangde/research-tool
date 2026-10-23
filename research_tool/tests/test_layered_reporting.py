"""Explicit evidence-with-limitations reporting never pretends gaps were resolved."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.export.wiki_stage import validate_current_run
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.research import assemble_report, gate_decision
from research_tool.tests.test_nine_loop_graph import graph_input
from research_tool.nine_loop.knowledge_min import build_evidence_graph
from research_tool.tests.test_nine_loop_runner import (
    QUOTE, config, model_reply,
    external_io as external_io,  # noqa: F401 - shared external-I/O pytest fixture
)


def inspection():
    return {"findings": [{"finding_id": "model:unresolved", "priority": "high",
                          "description": "No prospective challenge-specific estimate is available.",
                          "query": "prospective challenge-specific data", "claim_ids": []}],
            "resolutions": []}


def test_layered_contract_is_an_explicit_opt_in_not_a_changed_default():
    assert ReporterConfig().evidence_policy == "strict"
    assert ReporterConfig(evidence_policy="layered").evidence_policy == "layered"
    with pytest.raises(ValidationError):
        ReporterConfig(evidence_policy="ignore_all_gaps")


def test_strict_gate_stays_closed_and_layered_gate_only_reports_after_search_budget():
    issue = inspection()
    assert gate_decision(issue, max_high=0, max_total=0)["verdict"] == "CONTINUE"
    early = gate_decision(issue, max_high=0, max_total=0,
                          evidence_policy="layered", final_round=False)
    assert early["verdict"] == "CONTINUE"
    final = gate_decision(issue, max_high=0, max_total=0,
                          evidence_policy="layered", final_round=True)
    assert final["verdict"] == "STOP_SUCCESS"
    assert final["strict_verdict"] == "CONTINUE"
    assert final["high"] == 1 and final["total"] == 1
    assert final["report_contract"] == "evidence_with_limitations/v1"
    assert final["unresolved_findings_preserved"] is True
    assert issue["findings"][0]["finding_id"] == "model:unresolved"


@pytest.mark.asyncio
async def test_layered_report_keeps_gaps_coverage_and_known_limits_directly_visible():
    fact, src = graph_input()
    graph = build_evidence_graph([fact], [src])
    result = await assemble_report(
        graph, MockLLMClient(structured_response=model_reply), "test", 30000,
        evidence_policy="layered", inspection=inspection(), minimum_sources=3,
        known_limitations=["Missing patient-level timing prevents causal attribution."],
    )
    text = result["markdown"]
    assert "证据与局限分层" in text
    assert "model:unresolved" in text
    assert inspection()["findings"][0]["description"] in text
    assert "Missing patient-level timing prevents causal attribution." in text
    assert "单内容家族" in text
    assert "不等于独立研究" in text
    assert "evidence-audit.json" in text
    assert result["evidence_audit"]["unresolved_findings"] == inspection()["findings"]
    assert result["evidence_audit"]["coverage"][0]["content_family_count"] == 1
    assert result["evidence_audit"]["coverage"][0]["required_sources"] == 3
    assert result["evidence_audit"]["known_limitations"]
    assert result["claims"][0]["citations"][0]["text"] == fact["text"]


@pytest.mark.asyncio
async def test_exhausted_selection_correction_is_not_restarted_by_pipeline_stage_retries(
    tmp_path, external_io,
):
    cfg = config(tmp_path, minimum=2)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 1
    cfg.llm_stage_attempts = 3
    cfg.llm_retry_backoff_sec = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered", max_selected_claims=1)
    selection_calls = []

    def invalid_selection(prompt, schema):
        if schema.__name__ == "ReportSelection":
            selection_calls.append(prompt)
            return schema.model_validate({"claim_ids": ["F1", "F2"]})
        return model_reply(prompt, schema)

    pipe = ResearchPipeline(cfg, llm=MockLLMClient(structured_response=invalid_selection))
    result = await pipe.run()
    assert result.verdict == "STOP_ERROR" and result.failed_stage == "report"
    assert len(selection_calls) == 2
    summary = json.loads(result.run_summary_path.read_text())
    assert summary["stage_metrics"][-1]["diagnostic"]["reason"] == "selection_invalid"
    assert summary["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_content_filter_stops_report_without_stage_retry_or_correction(tmp_path, external_io):
    from research_tool.domain.errors import LLMStructuredOutputError
    from research_tool.domain.models import LLMFailureDiagnostic

    cfg = config(tmp_path, minimum=2)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 1
    cfg.llm_stage_attempts = 3
    cfg.llm_retry_backoff_sec = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered", max_selected_claims=1)
    attempts = []

    def filtered(prompt, schema):
        if schema.__name__ == "ReportSelection":
            attempts.append(prompt)
            raise LLMStructuredOutputError(LLMFailureDiagnostic(
                reason="content_filter", finish_reason="content_filter",
            ))
        return model_reply(prompt, schema)

    result = await ResearchPipeline(
        cfg, llm=MockLLMClient(structured_response=filtered),
    ).run()
    assert result.verdict == "STOP_ERROR" and result.failed_stage == "report"
    assert len(attempts) == 1
    summary = json.loads(result.run_summary_path.read_text())
    assert summary["stage_metrics"][-1]["diagnostic"]["reason"] == "content_filter"


@pytest.mark.asyncio
async def test_layered_prompts_include_source_context_and_limits_for_counterevidence_checks():
    fact, src = graph_input()
    prefix = "Table entry and narrative totals disagree; subgroup membership is not established.\n\n"
    src["text"] = prefix + src["text"]
    import hashlib
    src["content_sha256"] = hashlib.sha256(src["text"].encode()).hexdigest()
    span = fact["citations"][0]
    span.update(start_offset=len(prefix), end_offset=len(src["text"]),
                content_sha256=src["content_sha256"])
    graph = build_evidence_graph([fact], [src])
    captured = []

    def with_context(prompt, schema):
        if schema.__name__ in {"ReportSectionDraft", "ReportVerification", "ReportSectionRepair"}:
            captured.append(prompt)
            assert "subgroup membership is not established" in prompt
            assert "Do not confuse subgroup and total denominator." in prompt
        return model_reply(prompt, schema)

    report = await assemble_report(
        graph, MockLLMClient(structured_response=with_context), "test", 30000,
        evidence_policy="layered", inspection=inspection(),
        known_limitations=["Do not confuse subgroup and total denominator."],
    )
    assert len(captured) >= 2
    assert report["claims"][0]["citations"][0] == span


@pytest.mark.asyncio
async def test_layered_summary_can_select_supported_subset_and_audits_every_omission():
    a, sa = graph_input("a")
    b, sb = graph_input("b")
    graph = build_evidence_graph([a, b], [sa, sb])

    def choose_one(prompt, schema):
        if schema.__name__ == "ReportOutline":
            facts = json.loads(prompt.split("DATA:\n", 1)[1])["facts"]
            return schema(sections=[{"heading": "Observed results", "claim_ids": [facts[0]["claim_id"]]}])
        return model_reply(prompt, schema)

    report = await assemble_report(
        graph, MockLLMClient(structured_response=choose_one), "test", 30000,
        evidence_policy="layered", inspection={"findings": [], "resolutions": []},
    )
    audit = report["evidence_audit"]
    assert len(audit["selected_claim_ids"]) == 1
    assert len(audit["unselected_claim_ids"]) == 1
    assert set(audit["selected_claim_ids"] + audit["unselected_claim_ids"]) == {"a", "b"}
    assert "1/2" in report["markdown"]


@pytest.mark.asyncio
async def test_layered_selection_schema_exposes_hard_limit_and_repairs_once_without_truncation():
    inputs = [graph_input(str(index)) for index in range(8)]
    graph = build_evidence_graph([item[0] for item in inputs], [item[1] for item in inputs])
    attempts = []

    def too_many_once(prompt, schema):
        if schema.__name__ == "ReportSelection":
            attempts.append(prompt)
            assert schema.model_json_schema()["properties"]["claim_ids"]["maxItems"] == 2
            if len(attempts) == 1:
                # Boundary fake represents invalid model content; real parsing must reject it.
                return schema.model_validate({"claim_ids": ["F1", "F2", "F3"]})
            return schema(claim_ids=["F1", "F2"])
        return model_reply(prompt, schema)

    report = await assemble_report(
        graph, MockLLMClient(structured_response=too_many_once), "test", 30000,
        evidence_policy="layered", inspection=inspection(), max_selected_claims=2,
    )
    assert len(attempts) == 2
    assert len(report["evidence_audit"]["selected_claim_ids"]) == 2
    assert len(report["evidence_audit"]["unselected_claim_ids"]) == 6


@pytest.mark.asyncio
async def test_large_layered_ledger_selects_before_outline_and_preserves_all_unselected_ids():
    inputs = [graph_input(str(index)) for index in range(8)]
    graph = build_evidence_graph([item[0] for item in inputs], [item[1] for item in inputs])
    calls = []

    def select_then_outline(prompt, schema):
        calls.append(schema.__name__)
        data = json.loads(prompt.split("DATA:\n", 1)[1]) if "DATA:\n" in prompt else None
        if schema.__name__ == "ReportSelection":
            return schema(claim_ids=["F1", "F2"])
        if schema.__name__ == "ReportOutline":
            assert len(data["facts"]) == 2
        return model_reply(prompt, schema)

    report = await assemble_report(
        graph, MockLLMClient(structured_response=select_then_outline), "test", 30000,
        evidence_policy="layered", inspection=inspection(), max_selected_claims=2,
    )
    assert calls[:2] == ["ReportSelection", "ReportOutline"]
    assert len(report["evidence_audit"]["selected_claim_ids"]) == 2
    assert len(report["evidence_audit"]["unselected_claim_ids"]) == 6


@pytest.mark.asyncio
async def test_layered_model_uses_short_fact_references_and_persisted_claim_ids_stay_original():
    fact, src = graph_input()
    fact["claim_id"] = "a" * 64
    graph = build_evidence_graph([fact], [src])
    observed = []

    def inspect_prompt(prompt, schema):
        if schema.__name__ == "ReportOutline":
            data = json.loads(prompt.split("DATA:\n", 1)[1])
            observed.extend(x["claim_id"] for x in data["facts"])
            assert len(data["facts"][0]["claim_id"]) < 16
        return model_reply(prompt, schema)

    report = await assemble_report(
        graph, MockLLMClient(structured_response=inspect_prompt), "test", 30000,
        evidence_policy="layered", inspection=inspection(),
    )
    assert observed == ["F1"]
    assert report["claims"][0]["claim_ids"] == ["a" * 64]
    assert report["evidence_audit"]["selected_claim_ids"] == ["a" * 64]


@pytest.mark.asyncio
async def test_layered_report_requires_an_explicit_inspection_record():
    fact, src = graph_input()
    graph = build_evidence_graph([fact], [src])
    with pytest.raises(Exception, match="检视"):
        await assemble_report(graph, MockLLMClient(structured_response=model_reply),
                              "test", 30000, evidence_policy="layered")


@pytest.mark.asyncio
async def test_layered_mode_still_blocks_an_unverified_fact_span():
    fact, src = graph_input()
    fact["citations"][0]["end_offset"] -= 1
    graph = {"facts": [fact], "sources": [src], "nodes": []}
    with pytest.raises(Exception, match="引用"):
        await assemble_report(graph, MockLLMClient(), "test", 30000,
                              evidence_policy="layered", inspection=inspection())


@pytest.mark.asyncio
async def test_layered_pipeline_runs_search_and_persists_contract_without_mutating_canonical_record(
    tmp_path, external_io,
):
    cfg = config(tmp_path, minimum=2)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 1
    cfg.reporter = ReporterConfig(evidence_policy="layered", known_limitations=["Long-term risk unknown."])

    def unresolved(prompt, schema):
        if schema.__name__ == "ResearchInspection":
            return schema(**inspection())
        return model_reply(prompt, schema)

    pipe = ResearchPipeline(cfg, llm=MockLLMClient(structured_response=unresolved))
    result = await pipe.run()
    assert result.verdict == "STOP_SUCCESS"
    assert {"targeted", "merge", "report"}.issubset(result.stages_completed)
    summary, state, final = validate_current_run(result.topic_dir)
    assert summary["report_contract"] == "evidence_with_limitations/v1"
    assert summary["strict_evidence_complete"] is False
    assert summary["unresolved_finding_count"] >= 1
    assert final is not None
    audit = json.loads((final / "evidence-audit.json").read_text())
    assert audit["unresolved_findings"][0]["finding_id"] == "model:unresolved"
    assert audit["known_limitations"] == ["Long-term risk unknown."]
    assert audit["strict_evidence_complete"] is False
    assert f"runs/{summary['run_id']}/final/evidence-audit.json" in summary["files"]
    record = json.loads((result.topic_dir / summary["record_path"]).read_text())
    assert "report_contract" not in record  # exact external record fields unchanged
    assert QUOTE in (final / "report.md").read_text()
    assert "model:unresolved" in (final / "report.md").read_text()
    assert external_io


@pytest.mark.asyncio
async def test_layered_pipeline_resume_uses_same_verified_report_without_more_calls(tmp_path, external_io):
    cfg = config(tmp_path, minimum=2)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered")
    first = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert first.verdict == "STOP_SUCCESS"
    resumed = await ResearchPipeline(
        cfg, llm=MockLLMClient(structured_response=lambda *a: pytest.fail("resume called model")),
    ).run()
    assert resumed.verdict == "STOP_SUCCESS"
    assert resumed.report_result.report_path.read_bytes() == first.report_result.report_path.read_bytes()
