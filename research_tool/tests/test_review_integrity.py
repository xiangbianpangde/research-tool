"""A review cannot substitute new evidence bytes for already committed identities."""
from __future__ import annotations
import hashlib
import json

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.retrieval.review import record_review
from research_tool.tests.test_researchctl_grounded_contract import module_root, client
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


@pytest.mark.asyncio
async def test_review_rejects_an_uncommitted_snapshot_substitution(tmp_path, external_io):
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module_root()
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(result.run_summary_path.read_text())
    snapshot = result.topic_dir / 'runs' / summary['run_id'] / 'evidence-snapshot.json'
    snapshot.write_bytes(snapshot.read_bytes() + b' ')
    with pytest.raises(ValueError, match='hash|committed'):
        record_review(result.topic_dir, summary['run_id'], status='passed', reviewer='agent',
                      reason='Cannot assert source review on changed evidence.',
                      expected_report_sha256=hashlib.sha256(result.report_result.report_path.read_bytes()).hexdigest())
    assert client(result.topic_dir).index()['status'] == 'fail_closed'
