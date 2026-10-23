"""Medical type/evidence combinations must be closed validated choices before LLM selection."""
from research_tool.nine_loop.grounded_synthesis import build_observation_choices
from research_tool.tests.test_atomic_grounding import graph_for


def test_model_never_gets_untreated_negative_choice_for_untreated_pph():
    graph=graph_for(['Of 45 live births, 16 were complicated by PPH and none received prophylaxis.'])
    choices=build_observation_choices(graph['facts'],graph)
    assert choices
    assert not any(x['code']=='untreated_negative_challenge' for x in choices)
    assert any(x['code']=='challenge_bleeding' for x in choices)
    assert all(x['claim_ids']==['c0'] for x in choices)


def test_valid_negative_challenge_choice_keeps_original_exact_evidence():
    graph=graph_for(['Patients underwent surgery without prophylaxis and no abnormal bleeding occurred.'])
    choices=build_observation_choices(graph['facts'],graph)
    choice=next(x for x in choices if x['code']=='untreated_negative_challenge')
    assert choice['claim_ids']==['c0']
    assert choice['evidence_text']==graph['facts'][0]['text']
    assert choice['choice_id']
