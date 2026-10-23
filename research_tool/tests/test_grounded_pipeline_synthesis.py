"""Source-grounded Chinese reporting is a real nine-stage consumer, not just helper checks."""
import json
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import ReporterConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import module_root, client
from research_tool.tests.test_nine_loop_runner import config, model_reply, external_io as external_io  # noqa: F401


def grounded_reply(prompt, schema):
    if schema.__name__ == 'ObservationChoice':
        choices = json.loads(prompt.split('\nCHOICES:', 1)[1].split('\nFULL_SOURCE:', 1)[0])
        return schema(choice_ids=[next(c['choice_id'] for c in choices
                                      if c['code'] == 'study_observation')])
    if schema.__name__ == 'AtomChecks':
        atoms = json.loads(prompt.split('\nATOMS:', 1)[1].split('\nCOMPLETE_SOURCE', 1)[0])
        return schema(checks=[{'index': i, 'status': 'supported',
                               'supporting_ids': atom['claim_ids'], 'counterevidence_ids': [],
                               'reason': 'A bounded description of the cited experiment.'}
                              for i, atom in enumerate(atoms)])
    return model_reply(prompt, schema)


@pytest.mark.asyncio
async def test_source_grounded_chinese_pipeline_preserves_atomic_audit(tmp_path, external_io):
    cfg = config(tmp_path)
    cfg.reporter = ReporterConfig(evidence_policy='layered', grounding_policy='source_grounded')
    cfg.retrieval.module_root = module_root()
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=grounded_reply)).run()
    assert result.verdict == 'STOP_SUCCESS', result
    assert '原文报告下列研究内观察' in result.report_result.report_path.read_text()
    audit = json.loads((result.report_result.report_path.parent / 'grounding-audit.json').read_text())
    assert audit['contract'] == 'atomic-full-source/v1'
    assert all(x['semantic']['status'] == 'supported' for x in audit['atomic_checks'])
    assert client(result.topic_dir).index()['status'] == 'success'
