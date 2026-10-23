"""C02/C03/C07/C09: full-source lookup before any successful report exists."""
from __future__ import annotations
from pathlib import Path

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import StageError
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.retrieval.snapshot import write_evidence_snapshot
from research_tool.tests.test_researchctl_grounded_contract import client
from research_tool.tests.test_nine_loop_runner import (
    config, model_reply, external_io as external_io,  # noqa: F401
)


@pytest.mark.asyncio
async def test_snapshot_reads_committed_full_body_before_report_and_rejects_drift(tmp_path, external_io):
    class Paused(ResearchPipeline):
        async def _report(self, graph, inspection=None):
            stage = 'merge:0' if 'merge:0' in self._state['completed_stages'] else 'knowledge'
            self.snapshot = write_evidence_snapshot(self.root, self._state, stage)
            raise StageError('report', 'fixture stops before report')

    pipe = Paused(config(tmp_path), llm=MockLLMClient(structured_response=model_reply))
    result = await pipe.run()
    assert result.verdict == 'STOP_ERROR'
    c = client(result.topic_dir)
    caps = c.capabilities()
    assert 'evidence-snapshot/v1' in caps['contracts']
    data = c.evidence(pipe.snapshot, limit=1)
    assert data['status'] == 'success'
    assert data['scope']['report_exists'] is False
    assert data['scope']['source_count'] == 1
    assert data['results'] and data['results'][0]['text']
    assert data['scope']['matched_count'] >= len(data['results'])
    assert data['scope']['truncated'] is True
    methods = c.evidence(pipe.snapshot, text='Experimental methods and limitations.')
    assert methods['status'] == 'success' and methods['results']
    source = Path(data['results'][0]['clean_path'])
    assert source.is_file()
    assert data['results'][0]['content_sha256']
    graph = c.evidence(pipe.snapshot, view='graph')
    assert graph['status'] == 'success' and graph['results'][0]['graph']['facts']
    source.write_text(source.read_text() + 'tampered')
    assert c.evidence(pipe.snapshot, text='methods')['status'] == 'fail_closed'
    assert not list((result.topic_dir / 'runs').glob('*/final/report.md'))


@pytest.mark.asyncio
async def test_missing_capability_fails_closed_without_legacy_fallback(tmp_path, external_io):
    c = client(tmp_path)
    result = c.capabilities()
    assert result['layout'] == 'research-tool'
    # User-named main tree is intentionally older; do not silently use it.
    old = Path('/Users/xbpd/Projects/文字Agent检索系统')
    if old.exists():
        from research_tool.infrastructure.retrieval import ResearchCtlClient, RetrievalClientError
        with pytest.raises(RetrievalClientError):
            ResearchCtlClient(tmp_path, module_root=old).capabilities()
