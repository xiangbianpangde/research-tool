"""Free model conclusions cannot enter the controlled clinical observation interface."""
import pytest
from pydantic import ValidationError
from research_tool.nine_loop.grounded_synthesis import ObservationPlan, render_observation
from research_tool.tests.test_atomic_grounding import graph_for


def test_writer_selects_typed_evidence_not_unchecked_medical_text():
    with pytest.raises(ValidationError):
        ObservationPlan.model_validate({'observations': [{'code':'study_observation',
            'claim_ids':['c0'], 'text':'原文没有分母，所有患者终身安全。'}]})
    graph=graph_for(['32/102 patients had previously undergone surgery or childbirth without preventive treatment and no abnormal bleeding occurred.'])
    plan=ObservationPlan(observations=[{'code':'untreated_negative_challenge','claim_ids':['c0']}])
    rendered=render_observation(plan.observations[0],graph)
    assert '未经预防' in rendered['text'] and '已挑战' in rendered['text']
    assert '终身安全' in rendered['text'] and '不' in rendered['text']
    assert '32/102' not in rendered['text']  # numeric statement is rendered from exact evidence separately


def test_treated_negative_cannot_be_labelled_untreated_even_if_verifier_lies():
    from research_tool.domain.errors import StageError
    graph=graph_for(['Nine patients had no bleeding after surgery with preoperative fibrinogen infusions.'])
    with pytest.raises(StageError):
        render_observation(ObservationPlan(observations=[{
            'code':'untreated_negative_challenge','claim_ids':['c0']}]).observations[0],graph)


def test_without_symptoms_is_not_untreated_challenge_and_crude_is_not_adjusted():
    from research_tool.domain.errors import StageError
    graph=graph_for(['Patients without symptoms had no bleeding after surgery with prophylaxis.'])
    with pytest.raises(StageError):
        render_observation(ObservationPlan(observations=[{
            'code':'untreated_negative_challenge','claim_ids':['c0']}]).observations[0],graph)
    graph=graph_for(['Unadjusted OR 2.0.'])
    with pytest.raises(StageError):
        render_observation(ObservationPlan(observations=[{
            'code':'adjusted_association','claim_ids':['c0']}]).observations[0],graph)


def test_negated_treatment_and_bleeding_are_not_positive_template_conditions():
    from research_tool.domain.errors import StageError
    graph=graph_for(['Dental extraction without prophylaxis resulted in no bleeding.'])
    for code in ['treated_negative_challenge','challenge_bleeding']:
        with pytest.raises(StageError):
            render_observation(ObservationPlan(observations=[{
                'code':code,'claim_ids':['c0']}]).observations[0],graph)


def test_exposure_and_outcome_cannot_be_borrowed_across_different_events():
    from research_tool.domain.errors import StageError
    graph=graph_for(['Surgery without prophylaxis caused bleeding.',
                     'Dental extraction with prophylaxis was uneventful.'])
    with pytest.raises(StageError):
        render_observation(ObservationPlan(observations=[{
            'code':'untreated_negative_challenge','claim_ids':['c0','c1']}]).observations[0],graph)


@pytest.mark.parametrize('sentence,code', [
    ('Dental extraction without an infusion was uneventful.', 'treated_negative_challenge'),
    ('Dental extraction with no replacement was uneventful.', 'treated_negative_challenge'),
    ('Dental extraction without any prophylaxis had no bleeding.', 'treated_negative_challenge'),
    ('No postoperative bleeding occurred after dental extraction.', 'challenge_bleeding'),
    ('Dental extraction proceeded without abnormal bleeding.', 'challenge_bleeding'),
    ('Patients received prophylaxis before surgery, but there was no reduction in bleeding.', 'treated_negative_challenge'),
    ('Postoperative bleeding was not observed after surgery.', 'challenge_bleeding'),
    ('Bleeding did not occur after dental extraction.', 'challenge_bleeding'),
    ('Patients received prophylaxis before surgery but no bleeding reduction was observed.', 'treated_negative_challenge'),
    ('Patients received prophylaxis before surgery but no bleeding benefit was observed.', 'treated_negative_challenge'),
    ('Dental extraction without prophylaxis showed no bleeding reduction compared with baseline.', 'untreated_negative_challenge'),
    ('Untreated patients undergoing surgery showed no bleeding risk reduction.', 'untreated_negative_challenge'),
    ('Patients received prophylaxis before surgery but showed no bleeding risk reduction.', 'treated_negative_challenge'),
    ('Patients received prophylaxis before surgery, which was not uneventful.', 'treated_negative_challenge'),
    ('Surgery without prophylaxis was not described in the report as uneventful.', 'untreated_negative_challenge'),
])
def test_modifiers_do_not_hide_negative_treatment_or_outcome(sentence,code):
    from research_tool.domain.errors import StageError
    graph=graph_for([sentence])
    with pytest.raises(StageError):
        render_observation(ObservationPlan(observations=[{
            'code':code,'claim_ids':['c0']}]).observations[0],graph)


def test_affirmative_treatment_and_bleeding_types_remain_available():
    graph=graph_for(['The patients received preoperative fibrinogen infusions and surgery was uneventful.',
                     'Postoperative bleeding occurred after surgery.',
                     'Postoperative bleeding was not severe.',
                     'Bleeding was not associated with fibrinogen levels.'])
    for code,cid in [('treated_negative_challenge','c0'),('challenge_bleeding','c1'),
                     ('challenge_bleeding','c2'),('challenge_bleeding','c3')]:
        assert render_observation(ObservationPlan(observations=[{
            'code':code,'claim_ids':[cid]}]).observations[0],graph)['code'] == code


def test_source_conflict_template_never_guesses_a_subgroup():
    graph=graph_for(['Abstract: 20/35 were asymptomatic.', 'Figure: 10/35 were asymptomatic.'])
    plan=ObservationPlan(observations=[{'code':'source_conflict','claim_ids':['c0','c1']}])
    text=render_observation(plan.observations[0],graph)['text']
    assert '冲突' in text and '未解' in text and '子组' not in text
