"""Schema failures get one correction; explicit filtering gets no repeat request."""
import pytest
from research_tool.domain.errors import StageError, LLMStructuredOutputError
from research_tool.domain.models import LLMFailureDiagnostic
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.grounded_synthesis import synthesize_grounded_report
from research_tool.tests.test_atomic_grounding import graph_for


def inputs():
    graph = graph_for(['The source described a measured experiment.'])
    audit = {'selected_claim_ids': ['c0'], 'known_limitations': [], 'coverage': [],
             'total_claims': 1, 'unresolved_findings': []}
    bundle = {'source_blocks': [{'source_id': 'source', 'text': graph['sources'][0]['text']}]}
    return graph, audit, bundle


@pytest.mark.asyncio
async def test_invalid_atom_schema_is_corrected_once_and_exhaustion_is_terminal():
    graph, audit, bundle = inputs()
    calls = []
    audit_rows = []
    def invalid(prompt, schema):
        calls.append(schema.__name__)
        return schema.model_validate({'choice_ids': []})
    with pytest.raises(StageError) as caught:
        await synthesize_grounded_report(graph, MockLLMClient(structured_response=invalid),
                                         'test', audit, bundle, 30000,
                                         audit_callback=lambda rows: audit_rows.extend(rows))
    assert calls == ['ObservationChoice', 'ObservationChoice']
    assert audit_rows and audit_rows[-1]['status'] == 'rejected'
    assert audit_rows[-1]['failure_type'] == 'schema_invalid'
    assert caught.value.diagnostic.reason == 'selection_invalid'


@pytest.mark.asyncio
async def test_filtered_grounded_synthesis_never_enters_semantic_repair():
    graph, audit, bundle = inputs()
    calls = []
    rows = []
    def filtered(prompt, schema):
        calls.append(schema.__name__)
        raise LLMStructuredOutputError(LLMFailureDiagnostic(reason='content_filter'))
    with pytest.raises(LLMStructuredOutputError):
        await synthesize_grounded_report(graph, MockLLMClient(structured_response=filtered),
                                         'test', audit, bundle, 30000,
                                         audit_callback=lambda value: rows.extend(value))
    assert calls == ['ObservationChoice']
    assert rows and rows[-1]['status'] == 'failed'
    assert rows[-1]['diagnostic']['reason'] == 'content_filter'
