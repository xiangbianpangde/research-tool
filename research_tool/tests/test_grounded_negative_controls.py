"""Adversarial source-bound checks must survive a lying semantic verifier."""
from __future__ import annotations
import pytest
from research_tool.domain.errors import StageError
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.grounded_synthesis import synthesize_grounded_report
from research_tool.tests.test_atomic_grounding import graph_for


@pytest.mark.asyncio
async def test_approving_verifier_cannot_make_a_global_absence_statement_pass():
    graph = graph_for(['Outcome odds ratio was 5.8.', 'Methods: adjusted for age and parity.'])
    calls = []
    def reply(prompt, schema):
        calls.append(schema.__name__)
        if schema.__name__ == 'AtomDraft':
            return schema(atoms=[{'kind': 'description', 'text': '原文未报告调整变量。', 'claim_ids': ['c0']}])
        if schema.__name__ == 'AtomChecks':
            return schema(checks=[{'index': 0, 'status': 'supported', 'supporting_ids': ['c0'],
                                   'counterevidence_ids': [], 'reason': 'wrongly approves'}])
        raise AssertionError(schema.__name__)
    audit = {'selected_claim_ids': ['c0'], 'known_limitations': []}
    bundle = {'source_blocks': [{'source_id': 'source', 'text': graph['sources'][0]['text']}]}
    with pytest.raises(StageError):
        await synthesize_grounded_report(graph, MockLLMClient(structured_response=reply),
                                         'test', audit, bundle, 30000)
    assert calls == ['AtomDraft', 'AtomDraft']  # hard failure precedes the lying verifier


def test_negative_lifetime_limitation_is_not_a_positive_safety_claim():
    from research_tool.nine_loop.grounded_synthesis import check_atom
    graph = graph_for(['The retrospective study had limited follow-up.'])
    assert check_atom({'kind': 'description', 'text': '有限随访不能识别终身不出血。',
                       'claim_ids': ['c0']}, graph)['status'] == 'supported'


@pytest.mark.asyncio
async def test_verifier_receives_unselected_and_nonclaim_original_text():
    graph = graph_for(['Outcome odds ratio was 5.8.', 'Methods: adjusted for age and parity.'])
    full = graph['sources'][0]['text'] + '\nFigure: source context remains relevant.'
    seen = []
    def reply(prompt, schema):
        if schema.__name__ == 'AtomDraft':
            return schema(atoms=[{'kind': 'description', 'text': '原文报告了比值比。',
                                   'claim_ids': ['c0']}])
        if schema.__name__ == 'AtomChecks':
            seen.append(prompt)
            assert 'adjusted for age and parity' in prompt
            assert 'source context remains relevant' in prompt
            return schema(checks=[{'index': 0, 'status': 'supported', 'supporting_ids': ['c0'],
                                   'counterevidence_ids': [], 'reason': 'Exact effect, no risk ratio claim.'}])
        raise AssertionError(schema.__name__)
    audit = {'selected_claim_ids': ['c0'], 'known_limitations': [], 'coverage': [],
             'total_claims': 2, 'unresolved_findings': []}
    report = await synthesize_grounded_report(
        graph, MockLLMClient(structured_response=reply), 'test', audit,
        {'source_blocks': [{'source_id': 'source', 'text': full}]}, 30000,
    )
    assert seen and report['grounding_audit']['atomic_checks']
