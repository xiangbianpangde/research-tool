"""Task-required originals bypass neither validation nor the writer's audited evidence universe."""
import pytest
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.grounded_synthesis import synthesize_grounded_report
from research_tool.tests.test_atomic_grounding import graph_for


@pytest.mark.asyncio
async def test_required_conflict_and_denominator_literals_are_visible_even_if_writer_chooses_other():
    graph=graph_for(['Five women had ten pregnancies.', 'Figure: 10/35 asymptomatic.',
                     'Abstract: 20/35 asymptomatic.'])
    def reply(prompt,schema):
        if schema.__name__=='ObservationChoice':
            import json
            choices=json.loads(prompt.split('\nCHOICES:',1)[1].split('\nFULL_SOURCE:',1)[0])
            return schema(choice_ids=[next(c['choice_id'] for c in choices if c['code']=='study_observation')])
        if schema.__name__=='AtomChecks':
            import json
            atoms=json.loads(prompt.split('\nATOMS:',1)[1].split('\nCOMPLETE_SOURCE',1)[0])
            return schema(checks=[{'index':0,'status':'supported','supporting_ids':atoms[0]['claim_ids'],
                                   'counterevidence_ids':[],'reason':'Fixture observation supported'}])
        raise AssertionError(schema.__name__)
    audit={'selected_claim_ids':['c0'],'known_limitations':[],'coverage':[],'total_claims':3,'unresolved_findings':[]}
    bundle={'source_blocks':[{'source_id':'source','text':graph['sources'][0]['text']}],
            'required_evidence':[{'kind':'required','claim_ids':['c0']},
                                 {'kind':'source_conflict','claim_ids':['c1','c2']}]}
    result=await synthesize_grounded_report(graph,MockLLMClient(structured_response=reply),'test',audit,bundle,30000)
    assert '原文冲突未解' in result['markdown']
    for fact in graph['facts']:
        assert fact['text'] in result['markdown']
    assert set(result['evidence_audit']['selected_claim_ids'])=={'c0','c1','c2'}
