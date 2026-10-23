"""C02-C06: no model-approved semantic rewrite can override source-bound atomic evidence."""
from __future__ import annotations
import hashlib

import pytest

from research_tool.domain.errors import StageError
from research_tool.nine_loop.knowledge_min import build_evidence_graph
from research_tool.nine_loop.grounding import validate_atomic_statement, build_evidence_bundle


def graph_for(texts):
    body = '\n\n'.join(texts)
    digest = hashlib.sha256(body.encode()).hexdigest()
    sid = 'source'
    source = {'source_id': sid, 'text': body, 'locator': 'https://fixture.invalid/full',
              'content_sha256': digest, 'title': 'Original cohort'}
    facts = []
    for i, text in enumerate(texts):
        start = body.index(text)
        facts.append({'claim_id': f'c{i}', 'topic': 'results', 'text': text, 'citations': [{
            'source_id': sid, 'locator': source['locator'], 'content_sha256': digest,
            'start_offset': start, 'end_offset': start+len(text), 'text': text}]})
    return build_evidence_graph(facts, [source])


@pytest.mark.parametrize('rewrite', [
    'The paper did not report adjustment variables.',
    'Ten women experienced five bleeding events.',
    'The risk ratio was 5.8.',
    'Every patient is permanently safe.',
])
def test_wrong_verifier_approval_cannot_override_atomic_original(rewrite):
    graph = graph_for(['Five women had ten pregnancies and five miscarriages. Odds ratio 5.8.'])
    with pytest.raises(StageError, match='atomic|原文'):
        validate_atomic_statement({'text': rewrite, 'claim_ids': ['c0'],
                                   'supported': True}, graph)


def test_units_denominators_effects_are_not_detachable_from_the_literal_statement():
    graph = graph_for(['Five women had ten pregnancies. The odds ratio was 5.8 (95% CI 1.2–28.0).'])
    fact = graph['facts'][0]
    good = validate_atomic_statement({'text': fact['text'], 'claim_ids': ['c0']}, graph)
    assert good['text'] == fact['text'] and good['status'] == 'supported'
    for metadata in [{'unit': 'deliveries'}, {'effect_type': 'RR'}, {'denominator': 35}]:
        with pytest.raises(StageError):
            validate_atomic_statement({'text': fact['text'], 'claim_ids': ['c0'], **metadata}, graph)


def test_complete_source_bundle_keeps_distant_methods_unselected_facts_and_table_headers():
    texts = ['Outcome OR 5.8.', 'Unrelated background. ' * 600,
             'Methods: analyses adjusted for age and parity.',
             'Table 4 | Adjusted OR | 95% CI\nPrevious PPH | 5.43 | 0.70–43.55\nFootnote: per delivery.',
             'Five women had ten pregnancies, with five miscarriages.']
    graph = graph_for(texts)
    # All exact source blocks came from the pre-report reader, not a selected-only list.
    blocks = [{'source_id': 'source', 'text': t, 'start_offset': graph['sources'][0]['text'].index(t),
               'end_offset': graph['sources'][0]['text'].index(t)+len(t),
               'content_sha256': graph['sources'][0]['content_sha256'],
               'locator': graph['sources'][0]['locator']} for t in texts]
    bundle = build_evidence_bundle(graph, blocks, selected_ids=['c0'])
    assert any('adjusted for age and parity' in c['text'] for c in bundle['dependencies'])
    assert any('Previous PPH | 5.43' in c['text'] and 'Footnote:' in c['text'] for c in bundle['dependencies'])
    assert any('five miscarriages' in c['text'] for c in bundle['dependencies'])
    assert bundle['full_source_scope'] is True
    assert bundle['absence_inference_permitted'] is False


def test_conflicting_originals_remain_separate_and_are_never_reconciled_by_guessing():
    graph = graph_for(['Abstract: 20/35 were asymptomatic.', 'Figure: 10/35 were asymptomatic.',
                       'Narrative: 15/35 had no bleeding or thrombosis.'])
    for fact in graph['facts']:
        record = validate_atomic_statement({'text': fact['text'], 'claim_ids': [fact['claim_id']]}, graph)
        assert record['text'] == fact['text']
    with pytest.raises(StageError):
        validate_atomic_statement({'text': 'The 10/35 were a subgroup.', 'claim_ids': ['c1']}, graph)
