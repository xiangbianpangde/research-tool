"""Mandatory literal anchors and source conflicts cannot be omitted by writer selection."""
import pytest
from research_tool.domain.errors import StageError
from research_tool.nine_loop.grounded_synthesis import resolve_required_evidence, build_observation_choices
from research_tool.tests.test_atomic_grounding import graph_for


def test_required_anchor_binds_existing_exact_fact_and_missing_anchor_fails_closed():
    graph=graph_for(['Five women had ten pregnancies, with five miscarriages.',
                     'Figure: 10/35 were asymptomatic.', 'Abstract: 20/35 were asymptomatic.'])
    specs=[{'locator':'https://fixture.invalid/full','quotes':[graph['facts'][0]['text']],
            'kind':'required'},
           {'locator':'https://fixture.invalid/full','quotes':[graph['facts'][1]['text'],graph['facts'][2]['text']],
            'kind':'source_conflict'}]
    resolved=resolve_required_evidence(graph,specs)
    assert resolved[0]['claim_ids']==['c0']
    assert resolved[1]['kind']=='source_conflict'
    with pytest.raises(StageError):
        resolve_required_evidence(graph,[{'locator':'https://fixture.invalid/full','quotes':['invented quote'],'kind':'required'}])
    with pytest.raises(StageError):
        resolve_required_evidence(graph,[{'locator':'https://fixture.invalid/full',
            'quotes':[graph['facts'][1]['text'],graph['facts'][1]['text']],'kind':'source_conflict'}])


@pytest.mark.parametrize('text',[
    'Among 57 women given neuraxial anesthesia, none had a bleeding complication.',
    'Postpartum haemorrhage was effectively avoided by replacement.',
    'No significant difference in PPH rates was found between subtypes.',
    'The study investigated bleeding severity using retrospective data.',
    'Patients would have experienced bleeding without prophylaxis.',
    'If patients had surgery, they would have experienced bleeding.',
    'Patients may have experienced bleeding during delivery.',
    'It is unclear whether patients experienced bleeding.',
])
def test_nonpositive_bleeding_statements_do_not_get_positive_event_choice(text):
    graph=graph_for([text])
    choices=build_observation_choices(graph['facts'],graph)
    assert not any(c['code']=='challenge_bleeding' for c in choices)
    assert any(c['code']=='study_observation' for c in choices)
