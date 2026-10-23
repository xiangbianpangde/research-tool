"""Uncommitted snapshot preparation may recover; committed snapshot loss must fail closed."""
import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import StageError
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import config, model_reply, external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_uncommitted_snapshot_prep_recovers_after_publish_before_stage_commit(tmp_path, external_io):
    class Interrupted(ResearchPipeline):
        def _initialize(self):
            super()._initialize()
            original = self.runtime.commit_stage
            def interrupt(stage, *args):
                if stage == 'qgate:100':
                    raise StageError('report', 'interrupted before snapshot preparation commit')
                return original(stage, *args)
            self.runtime.commit_stage = interrupt
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module_root()
    failed = await Interrupted(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert failed.verdict == 'STOP_ERROR'
    summary = json.loads(failed.run_summary_path.read_text())
    initial = failed.topic_dir / 'runs' / summary['run_id'] / 'evidence-initial.json'
    assert initial.exists()
    # Torn uncommitted preparation is regenerated from committed evidence, never trusted.
    initial.write_text('{partial')
    restored = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert restored.verdict == 'STOP_SUCCESS'


@pytest.mark.asyncio
async def test_committed_snapshot_preparation_cannot_be_replaced_when_file_disappears(tmp_path, external_io):
    class Interrupted(ResearchPipeline):
        async def _ground_evidence(self, graph, inspection):
            await super()._ground_evidence(graph, inspection)
            raise StageError('report', 'stop after committed evidence prep')
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact')
    cfg.retrieval.module_root = module_root()
    failed = await Interrupted(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(failed.run_summary_path.read_text())
    initial = failed.topic_dir / 'runs' / summary['run_id'] / 'evidence-initial.json'
    initial.unlink()
    restored = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert restored.verdict == 'STOP_ERROR'
    assert not initial.exists()
