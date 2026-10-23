"""C05: source tokens existing somewhere are not sufficient numeric relation support."""
import pytest
from research_tool.domain.errors import StageError
from research_tool.nine_loop.grounded_synthesis import check_atom
from research_tool.tests.test_atomic_grounding import graph_for


def test_count_unit_metadata_cannot_hide_a_different_textual_numeric_unit():
    graph = graph_for(['Five women had 10 pregnancies, with five miscarriages.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'count', 'claim_ids': ['c0'], 'text': '10名女性经历妊娠。',
                    'quantity': '10', 'unit': 'pregnancies'}, graph)


def test_effect_values_cannot_be_swapped_between_or_and_hr_or_ci():
    graph = graph_for(['Odds ratio was 5.8 (95% CI 1.2–28.0); hazard ratio was 1.2.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': 'OR为1.2。',
                    'quantity': '1.2', 'effect_type': 'OR'}, graph)


def test_counts_cannot_reuse_all_source_numbers_in_a_reversed_fraction():
    graph = graph_for(['There were 12 PPH events among 34 live births of 54 pregnancies.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'count', 'claim_ids': ['c0'], 'text': '共34/12次活产。',
                    'quantity': '34', 'unit': 'live_births'}, graph)


def test_followup_for_two_years_is_not_an_or_point_estimate():
    graph = graph_for(['OR 1.5; follow-up for 2 years.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': 'OR为2。',
                    'quantity': '2', 'effect_type': 'OR'}, graph)


def test_unadjusted_or_cannot_be_relabelled_adjusted_even_if_other_methods_are_adjusted():
    graph = graph_for(['Unadjusted OR 1.5. Adjusted HR 2.0.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': '调整后OR为1.5。',
                    'quantity': '1.5', 'effect_type': 'OR'}, graph)


def test_conflict_atom_must_preserve_competing_source_values():
    graph = graph_for(['Abstract: 20/35 were asymptomatic.', 'Figure: 10/35 were asymptomatic.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'source_conflict', 'claim_ids': ['c0', 'c1'],
                    'text': '人数有冲突，但本报告采用20/35。'}, graph)


def test_fraction_cannot_replace_a_live_birth_denominator_with_pregnancies():
    graph = graph_for(['PPH was 12/34 live births, from 54 pregnancies.'])
    good = {'kind': 'fraction', 'claim_ids': ['c0'], 'text': 'PPH为12/34次活产。',
            'quantity': '12/34', 'unit': 'live_births'}
    assert check_atom(good, graph)['status'] == 'supported'
    with pytest.raises(StageError):
        check_atom({**good, 'text': 'PPH为12/54次妊娠。', 'quantity': '12/54',
                    'unit': 'pregnancies'}, graph)


def test_adjusted_flag_and_ci_cannot_be_borrowed_from_adjacent_estimates():
    graph = graph_for(['Adjusted OR 2.0. Crude OR 1.5.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': '调整后OR为1.5。',
                    'quantity': '1.5', 'effect_type': 'OR'}, graph)
    graph = graph_for(['OR 1.5 (95% CI 1.1–2.0).', 'OR 3.0 (95% CI 2.0–4.0).'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0', 'c1'],
                    'text': 'OR为1.5（95%CI 2.0–4.0）。', 'quantity': '1.5', 'effect_type': 'OR'}, graph)


def test_ci_cannot_be_swapped_between_two_estimates_in_one_sentence():
    graph = graph_for(['Unadjusted OR 2.0 (95% CI 1.1–3.6), adjusted OR 1.5 (95% CI 1.0–2.2).'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'],
                    'text': '未调整OR为2.0（95%CI 1.0–2.2）。',
                    'quantity': '2.0', 'effect_type': 'OR'}, graph)


def test_relative_risk_spelling_also_bounds_adjacent_ci():
    graph = graph_for(['Unadjusted RR 1.2 (95% CI 1.0–1.4), adjusted relative risk 1.8 (95% CI 1.5–2.1).'])
    base = {'kind': 'effect', 'claim_ids': ['c0'], 'text': '未调整RR为1.2（95%CI 1.0–1.4）。',
            'quantity': '1.2', 'effect_type': 'RR'}
    assert check_atom(base, graph)['status'] == 'supported'
    with pytest.raises(StageError):
        check_atom({**base, 'text': '未调整RR为1.2（95%CI 1.5–2.1）。'}, graph)


@pytest.mark.parametrize('joiner', [' and ', ', and '])
def test_ci_is_bound_to_effect_occurrence_not_conjunction(joiner):
    graph = graph_for(['OR 1.5 (95% CI 1.1–2.0)' + joiner
                       + 'relative risk 1.2 (95% CI 1.05–1.4).'])
    base = {'kind': 'effect', 'claim_ids': ['c0'], 'text': 'RR为1.2（95%CI 1.05–1.4）。',
            'quantity': '1.2', 'effect_type': 'RR'}
    assert check_atom(base, graph)['status'] == 'supported'
    with pytest.raises(StageError):
        check_atom({**base, 'text': 'RR为1.2（95%CI 1.1–2.0）。'}, graph)


@pytest.mark.parametrize('marker', ['95% CI (1.2–1.8)', '95%CI［1.2–1.8］', '95%置信区间（1.2–1.8）'])
def test_ci_marker_with_brackets_or_chinese_label_cannot_skip_binding(marker):
    graph = graph_for(['Unadjusted OR 2.0 (95% CI 1.1–3.0) and adjusted OR 1.5 (95% CI 1.2–1.8).'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': '未调整OR为2.0，' + marker,
                    'quantity': '2.0', 'effect_type': 'OR'}, graph)


def test_effect_ci_endpoints_cannot_be_reversed():
    graph = graph_for(['Adjusted OR 5.43 (95% CI 0.70–43.55).'])
    with pytest.raises(StageError):
        check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': 'OR为5.43（95%CI 43.55–0.70）。',
                    'quantity': '5.43', 'effect_type': 'OR'}, graph)


def test_unsupported_extra_medical_clause_must_not_hide_inside_a_correct_count():
    graph = graph_for(['There were 10 pregnancies.'])
    with pytest.raises(StageError):
        check_atom({'kind': 'count', 'claim_ids': ['c0'], 'text': '共10次妊娠，且终身安全。',
                    'quantity': '10', 'unit': 'pregnancies'}, graph)
