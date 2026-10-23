"""Consumer must bind decision chain, sidecar and report review without independent writes."""

import hashlib
import json
import copy
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import DispositionConfig, RetrievalConfig
from research_tool.infrastructure.disposition import Registry
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.retrieval.review import record_review
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401
from research_tool.tests.test_researchctl_grounded_contract import client, module_root


@pytest.mark.asyncio
async def test_sidecar_decision_hash_and_review_versions_are_required(tmp_path, external_io):
    cfg = config(tmp_path / "consumer")
    reg = Registry.create(
        tmp_path / "registry", registry_id="consumer-binding", roots={"test": str(tmp_path)}
    )
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_SUCCESS"
    final = result.report_result.report_path.parent
    evaluation = json.loads((final / "issue-evaluation.json").read_text())
    import sys

    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evaluation import verify
    from researchctl.layouts.research_tool import LayoutError

    broken = copy.deepcopy(evaluation)
    broken["decision_snapshot"]["state"]["registry_id"] = "changed"
    with pytest.raises(LayoutError):
        verify(broken)
    broken = copy.deepcopy(evaluation)
    broken["network_sha256"] = "a" * 64
    with pytest.raises(LayoutError):
        verify(broken)
    broken = copy.deepcopy(evaluation)
    broken.pop("counts")
    with pytest.raises(LayoutError):
        verify(broken)
    broken = copy.deepcopy(evaluation)
    broken["required_unresolved_total"] += 1
    with pytest.raises(LayoutError):
        verify(broken)
    c = client(result.topic_dir)
    assert c.index()["status"] == "success"
    summary = json.loads(result.run_summary_path.read_text())
    path = record_review(
        result.topic_dir,
        summary["run_id"],
        status="passed",
        reviewer="agent",
        reason="Version-bound read-only engineering review, not clinical approval",
        expected_report_sha256=hashlib.sha256(
            result.report_result.report_path.read_bytes()
        ).hexdigest(),
    )
    review = json.loads(path.read_text())
    assert review["issue_head_sha256"] == reg.head()
    assert c.index()["status"] == "success"
    assert c.query(entity="CURRENT", require_review="agent_source_review")["status"] == "success"
    review.pop("issue_head_sha256")
    path.write_text(json.dumps(review))
    assert c.index()["status"] == "fail_closed"
