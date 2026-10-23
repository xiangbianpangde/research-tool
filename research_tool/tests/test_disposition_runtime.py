"""A15: reviewed is explicit, hash-pinned and cannot hide unclassified limitations."""

import hashlib
import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig, DispositionConfig, RetrievalConfig
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.disposition import Registry
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.mark.asyncio
async def test_raw_default_and_reviewed_layered_report_bind_dispositions_without_closing_gaps(
    tmp_path, external_io
):
    assert DispositionConfig().policy == "raw"
    cfg = config(tmp_path / "origin", minimum=2)
    cfg.reporter = ReporterConfig(
        evidence_policy="layered", known_limitations=["Future absolute risk remains unknown."]
    )
    original = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(original.run_summary_path.read_text())
    audit = original.report_result.report_path.parent / "evidence-audit.json"
    reg = Registry.create(
        tmp_path / "registry", registry_id="runtime", roots={"origin": str(original.topic_dir)}
    )
    reg.import_audit(
        root_id="origin",
        run_id=summary["run_id"],
        expected_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),
        expected_head=reg.head(),
        idempotency_key="import",
        actor="agent",
    )
    cfg.work_dir = tmp_path / "consumer"
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_SUCCESS"
    final = result.report_result.report_path.parent
    assert (final / "issue-evaluation.json").is_file()
    evaluation = json.loads((final / "issue-evaluation.json").read_text())
    assert evaluation["required_unresolved_total"] > 0
    assert evaluation["hard_evidence_checks_waivable"] is False
    after = json.loads(result.run_summary_path.read_text())
    assert after["required_contracts"] == ["issue-evaluation/v1"]
    assert after["issue_head_sha256"] == reg.head()
    assert "问题处置" in result.report_result.report_path.read_text()
    record = json.loads((result.topic_dir / after["record_path"]).read_text())
    assert "required_contracts" not in record and len(record["files"]) == 4
    no_calls = MockLLMClient(structured_response=lambda *_: pytest.fail("resume called model"))
    assert (await ResearchPipeline(cfg, llm=no_calls).run()).verdict == "STOP_SUCCESS"
    cfg.reporter = ReporterConfig(evidence_policy="strict")
    cfg.nine_loop.max_rounds = 0
    stopped = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert stopped.verdict == "STOP_BUDGET"
    assert stopped.report_result is None


@pytest.mark.asyncio
async def test_reviewed_source_grounded_uses_latest_context_inspection(tmp_path, external_io):
    from research_tool.tests.test_grounded_pipeline_synthesis import grounded_reply
    from research_tool.tests.test_researchctl_grounded_contract import client

    cfg = config(tmp_path / "consumer")
    reg = Registry.create(
        tmp_path / "registry", registry_id="grounded-reviewed", roots={"root": str(tmp_path)}
    )
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    cfg.reporter = ReporterConfig(evidence_policy="layered", grounding_policy="source_grounded")
    result = await ResearchPipeline(
        cfg, llm=MockLLMClient(structured_response=grounded_reply)
    ).run()
    assert result.verdict == "STOP_SUCCESS", result
    assert client(result.topic_dir).index()["status"] == "success"
    summary = json.loads(result.run_summary_path.read_text())
    record = json.loads((result.topic_dir / summary["record_path"]).read_text())
    env = json.loads(
        (
            result.topic_dir / record["runtime"]["completed_stages"]["report"]["artifact_path"]
        ).read_text()
    )
    assert env["result"]["issue_inputs"]["inspection_stage_key"] == "inspect:100"


@pytest.mark.asyncio
async def test_strict_cannot_publish_new_context_gap_after_original_finding_corrected(
    tmp_path, external_io
):
    from research_tool.tests.test_grounded_pipeline_synthesis import grounded_reply
    from research_tool.tests.test_disposition_transitions import classify_wording, decide

    cfg = config(tmp_path / "origin", minimum=2)
    cfg.nine_loop.max_rounds = 0
    cfg.reporter = ReporterConfig(evidence_policy="layered")
    original = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(original.run_summary_path.read_text())
    final = original.report_result.report_path.parent
    graph = json.loads((final / "tree/knowledge.json").read_text())
    reg = Registry.create(
        tmp_path / "registry", registry_id="context-gate", roots={"corpus": str(original.topic_dir)}
    )
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
    issue = classify_wording(reg, issue, ref)
    decide(
        reg,
        issue,
        "corrected",
        {"rationale": "Scoped legacy wording correction only", "evidence_refs": [ref]},
        "correct",
    )
    cfg.work_dir = tmp_path / "consumer"
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    cfg.reporter = ReporterConfig(evidence_policy="strict", grounding_policy="source_grounded")
    result = await ResearchPipeline(
        cfg, llm=MockLLMClient(structured_response=grounded_reply)
    ).run()
    assert result.verdict != "STOP_SUCCESS"
    assert result.report_result is None


@pytest.mark.asyncio
async def test_unsupported_registry_head_fails_without_current_success(tmp_path, external_io):
    cfg = config(tmp_path)
    reg = Registry.create(tmp_path / "registry", registry_id="head", roots={"root": str(tmp_path)})
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256="0" * 64
    )
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_ERROR" and result.report_result is None
