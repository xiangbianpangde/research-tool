"""Long ledger IDs are immutable, but model-facing evidence identifiers must be short and closed."""
import json
import pytest
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.grounded_synthesis import synthesize_grounded_report, build_observation_choices
from research_tool.tests.test_atomic_grounding import graph_for


@pytest.mark.asyncio
async def test_verifier_only_sees_short_ids_and_persisted_trace_restores_originals():
    graph=graph_for(['The cohort had experienced bleeding.'])
    original='a'*64
    graph['facts'][0]['claim_id']=original
    graph['nodes'][0]['claim_ids']=[original]
    def reply(prompt,schema):
        if schema.__name__=='ObservationChoice':
            choices=json.loads(prompt.split('\nCHOICES:',1)[1].split('\nFULL_SOURCE:',1)[0])
            return schema(choice_ids=[next(c['choice_id'] for c in choices if c['code']=='study_observation')])
        if schema.__name__=='AtomChecks':
            assert original not in prompt
            atoms=json.loads(prompt.split('\nATOMS:',1)[1].split('\nCOMPLETE_SOURCE',1)[0])
            assert atoms[0]['claim_ids']==['E1']
            return schema(checks=[{'index':0,'status':'supported','supporting_ids':['E1'],
                'counterevidence_ids':[],'reason':'Source observation bound.'}])
        raise AssertionError(schema.__name__)
    audit={'selected_claim_ids':[original],'known_limitations':[],'coverage':[],
           'total_claims':1,'unresolved_findings':[]}
    report=await synthesize_grounded_report(graph,MockLLMClient(structured_response=reply),'test',audit,
          {'source_blocks':[{'source_id':'source','text':graph['sources'][0]['text']}]},30000)
    assert report['claims'][0]['claim_ids']==[original]
    assert report['grounding_audit']['atomic_checks'][0]['semantic']['supporting_ids']==[original]


def test_lowercase_or_conjunction_does_not_claim_an_effect_measure():
    graph=graph_for(['The proportion after surgery or delivery was 20%.'])
    choices=build_observation_choices(graph['facts'],graph)
    assert any(c['code']=='unadjusted_association' for c in choices)


def test_adjusted_effect_cannot_have_a_crude_choice_when_method_is_same_source():
    graph=graph_for(['Analyses were adjusted for age and parity.',
        'Women with a bleeding phenotype had increased PPH (OR 5.8; 95% CI 1.2–28.0; P=.03).'])
    choices=build_observation_choices(graph['facts'],graph)
    assert not any(c['claim_ids']==['c1'] and c['code']=='unadjusted_association' for c in choices)
    assert any(c['claim_ids']==['c1'] and c['code']=='study_observation' for c in choices)
