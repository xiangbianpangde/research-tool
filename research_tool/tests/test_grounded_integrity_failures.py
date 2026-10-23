"""Snapshot/sidecar identity is a hard boundary, not merely a successful query label."""
from __future__ import annotations
import json

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import client, module_root
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


@pytest.mark.asyncio
async def test_uncommitted_file_in_final_is_rejected_without_hiding_in_summary(tmp_path, external_io):
    result = await ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=model_reply)).run()
    (result.report_result.report_path.parent / 'injected.txt').write_text('not committed')
    assert client(result.topic_dir).index()['status'] == 'fail_closed'


@pytest.mark.asyncio
async def test_changed_initial_snapshot_is_not_trusted_on_grounding_resume(tmp_path, external_io):
    from research_tool.domain.errors import StageError
    class Interrupted(ResearchPipeline):
        async def _ground_evidence(self, graph, inspection):
            await super()._ground_evidence(graph, inspection)
            raise StageError('report', 'stop before report')
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module_root()
    first = await Interrupted(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(first.run_summary_path.read_text())
    snapshot = first.topic_dir / 'runs' / summary['run_id'] / 'evidence-initial.json'
    snapshot.write_bytes(snapshot.read_bytes() + b' ')
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == 'STOP_ERROR' and result.failed_stage == 'report'
    assert result.report_result is None
