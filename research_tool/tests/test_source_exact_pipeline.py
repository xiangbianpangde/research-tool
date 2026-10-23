"""Real nine-stage + real ResearchCTL reader, source-literal final without semantic rewrites."""
from __future__ import annotations
import hashlib
import json

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import module_root, client
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


@pytest.mark.asyncio
async def test_pre_report_source_exact_pipeline_records_full_evidence_and_resumes_without_calls(tmp_path, external_io):
    cfg = config(tmp_path, minimum=1)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_exact',
                                  known_limitations=['Missing supplements were not searched.'])
    cfg.retrieval.module_root = module_root()
    llm = MockLLMClient(structured_response=model_reply)
    result = await ResearchPipeline(cfg, llm=llm).run()
    assert result.verdict == 'STOP_SUCCESS', result
    summary = json.loads(result.run_summary_path.read_text())
    final = result.report_result.report_path.parent
    audit = json.loads((final / 'grounding-audit.json').read_text())
    graph = json.loads((final / 'tree/knowledge.json').read_text())
    assert audit['contract'] == 'source-exact-grounding/v1'
    assert audit['full_source_scope'] is True
    assert audit['unregistered_model_assertions'] == 0
    assert audit['absence_inference_permitted'] is False
    assert audit['dependencies']
    assert audit['atomic_checks'] and all(x['status'] == 'supported' for x in audit['atomic_checks'])
    assert all(check['text'] in [f['text'] for f in graph['facts']] for check in audit['atomic_checks'])
    assert hashlib.sha256((result.topic_dir / 'runs' / summary['run_id'] / 'evidence-snapshot.json').read_bytes()).hexdigest() == audit['snapshot_sha256']
    c = client(result.topic_dir)
    assert c.index()['status'] == 'success'
    assert c.trace('CURRENT')['status'] == 'success'
    assert c.query(entity='CURRENT', require_review='agent_source_review')['status'] == 'fail_closed'
    cfg.resume = True
    no_calls = MockLLMClient(structured_response=lambda *_: pytest.fail('resume called model'))
    restored = await ResearchPipeline(cfg, llm=no_calls).run()
    assert restored.verdict == 'STOP_SUCCESS'
    assert restored.report_result.report_path.read_bytes() == result.report_result.report_path.read_bytes()
    assert c.index()['status'] == 'success'
    assert c.trace('CURRENT')['status'] == 'success'
    restored_summary = json.loads(restored.run_summary_path.read_text())
    assert 'merge' in restored_summary['stages_completed']
    assert 'merge' not in restored_summary['stages_skipped']
