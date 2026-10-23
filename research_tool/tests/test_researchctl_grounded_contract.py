"""Real producer/consumer checks; only search/model I/O is faked, never layout parsing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.retrieval import ResearchCtlClient
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


def module_root():
    path = Path(os.environ.get('RESEARCHCTL_TEST_MODULE_ROOT', '/Users/xbpd/Projects/researchctl-layout'))
    if not (path / 'researchctl/layouts/research_tool.py').is_file():
        pytest.skip('Set RESEARCHCTL_TEST_MODULE_ROOT to the pinned ResearchCTL checkout')
    return path


def client(root):
    return ResearchCtlClient(root, module_root=module_root(), timeout_sec=30)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['strict', 'layered'])
async def test_real_producer_final_is_consumed_without_rewriting_records(tmp_path, external_io, policy):
    cfg = config(tmp_path, minimum=1)
    cfg.reporter = ReporterConfig(evidence_policy=policy)
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == 'STOP_SUCCESS'
    c = client(result.topic_dir)
    assert c.index()['status'] == 'success'
    assert c.query(entity='CURRENT')['status'] == 'success'
    assert c.sources('CURRENT')['status'] == 'success'
    assert c.trace('CURRENT')['status'] == 'success'
    assert c.reconcile()['status'] == 'success'
    summary = json.loads(result.run_summary_path.read_text())
    record = json.loads((result.topic_dir / summary['record_path']).read_text())
    assert len(record['files']) == 4
    assert len(summary['files']) > 4
    assert not {'review', 'report_contract', 'medical_accepted'} & set(record)
    extra = result.report_result.report_path.parent / 'not-committed.json'
    extra.write_text('{}')
    summary['files'][str(extra.relative_to(result.topic_dir))] = hashlib.sha256(extra.read_bytes()).hexdigest()
    result.run_summary_path.write_text(json.dumps(summary))
    assert c.reconcile()['status'] == 'fail_closed'


@pytest.mark.asyncio
async def test_review_adoption_is_pinned_independent_and_never_falls_back(tmp_path, external_io):
    from research_tool.infrastructure.retrieval.review import record_review

    result = await ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=model_reply)).run()
    c = client(result.topic_dir)
    assert c.capabilities()['status'] == 'success'
    assert c.index()['status'] == 'success'
    assert c.query(entity='CURRENT', require_review='agent_source_review')['status'] == 'fail_closed'
    summary = json.loads(result.run_summary_path.read_text())
    pins = dict(summary['files'])
    review = record_review(result.topic_dir, summary['run_id'], status='rejected', reviewer='test-agent',
                           reason='Exact quotations did not support the interpretation.',
                           expected_report_sha256=hashlib.sha256(result.report_result.report_path.read_bytes()).hexdigest())
    assert review.is_file()
    assert c.index()['status'] == 'success'
    raw = c.query(entity='CURRENT')
    assert raw['status'] == 'success'
    assert raw['results'][0]['review']['status'] == 'rejected'
    assert raw['results'][0]['status']['research'] != 'fresh'
    assert c.query(entity='CURRENT', require_review='agent_source_review')['status'] == 'fail_closed'
    assert json.loads(result.run_summary_path.read_text())['files'] == pins
    with pytest.raises(ValueError, match='hash|changed'):
        record_review(result.topic_dir, summary['run_id'], status='passed', reviewer='test-agent',
                      reason='Wrong identity must not be accepted.', expected_report_sha256='0' * 64)
    assert c.query(entity='CURRENT', require_review='human_clinical')['status'] == 'fail_closed'


@pytest.mark.asyncio
async def test_adoption_rejects_explicit_historical_success_when_current_unreviewed(tmp_path, external_io):
    from research_tool.infrastructure.retrieval.review import record_review
    cfg = config(tmp_path)
    first = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(first.run_summary_path.read_text())
    record_review(first.topic_dir, summary['run_id'], status='passed', reviewer='agent',
                  reason='Old fixture review',
                  expected_report_sha256=hashlib.sha256(first.report_result.report_path.read_bytes()).hexdigest())
    cfg.resume = False
    second = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    c = client(second.topic_dir)
    assert c.index()['status'] == 'success'
    assert c.query(entity=summary['run_id'], require_review='agent_source_review')['status'] == 'fail_closed'
    assert c.query(require_review='agent_source_review')['status'] == 'fail_closed'


@pytest.mark.asyncio
async def test_explicit_review_pass_is_not_a_human_clinical_acceptance(tmp_path, external_io):
    from research_tool.infrastructure.retrieval.review import record_review

    result = await ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(result.run_summary_path.read_text())
    record_review(result.topic_dir, summary['run_id'], status='passed', reviewer='test-agent',
                  reason='Fixture source spans and interpretations checked.',
                  expected_report_sha256=hashlib.sha256(result.report_result.report_path.read_bytes()).hexdigest())
    c = client(result.topic_dir)
    assert c.index()['status'] == 'success'
    accepted = c.query(entity='CURRENT', require_review='agent_source_review')
    assert accepted['status'] == 'success'
    assert accepted['results'][0]['review']['level'] == 'agent_source_review'
    assert c.query(entity='CURRENT', require_review='human_clinical')['status'] == 'fail_closed'
