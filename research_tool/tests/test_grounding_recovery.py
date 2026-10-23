"""Interruptions must not make immutable pre-report snapshots impossible to resume."""
from __future__ import annotations
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import StageError
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


@pytest.mark.asyncio
async def test_resume_after_dependencies_merged_but_before_final_report(tmp_path, external_io):
    class Interrupted(ResearchPipeline):
        async def _ground_evidence(self, graph, inspection):
            await super()._ground_evidence(graph, inspection)
            raise StageError('report', 'fixture interrupted after committed dependencies')

    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module_root()
    failed = await Interrupted(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert failed.verdict == 'STOP_ERROR'
    restored = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert restored.verdict == 'STOP_SUCCESS', restored
