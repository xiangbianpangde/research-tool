"""Reader must explicitly handshake and validate the pinned disposition sidecar."""

import hashlib
import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig, DispositionConfig, RetrievalConfig
from research_tool.infrastructure.disposition import Registry
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import client, module_root
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.mark.asyncio
async def test_real_reader_exposes_disposition_contract_and_exact_head(tmp_path, external_io):
    cfg = config(tmp_path / "origin")
    cfg.reporter = ReporterConfig(evidence_policy="layered", known_limitations=["No causal score."])
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    s = json.loads(result.run_summary_path.read_text())
    audit = result.report_result.report_path.parent / "evidence-audit.json"
    reg = Registry.create(
        tmp_path / "issues", registry_id="reader", roots={"origin": str(result.topic_dir)}
    )
    reg.import_audit(
        root_id="origin",
        run_id=s["run_id"],
        expected_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),
        expected_head=reg.head(),
        idempotency_key="i",
        actor="agent",
    )
    cfg.work_dir = tmp_path / "reviewed"
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    reviewed = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    c = client(reviewed.topic_dir)
    assert "issue-evaluation/v1" in c.capabilities()["contracts"]
    assert c.index()["status"] == "success"
    item = c.query(entity="CURRENT")["results"][0]
    assert item["issue_evaluation"]["issue_head_sha256"] == reg.head()
    assert item["issue_evaluation"]["required_unresolved_total"] > 0
    assert c.trace("CURRENT")["status"] == "success"
    summary = json.loads(reviewed.run_summary_path.read_text())
    summary["required_contracts"] = ["unknown-disposition/v99"]
    reviewed.run_summary_path.write_text(json.dumps(summary))
    assert c.index()["status"] == "fail_closed"
