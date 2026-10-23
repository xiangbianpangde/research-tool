"""Atomic Chinese synthesis must be checked beyond an approving model verifier."""
from __future__ import annotations
import pytest
from research_tool.domain.errors import StageError
from research_tool.nine_loop.grounded_synthesis import check_atom
from research_tool.tests.test_atomic_grounding import graph_for


def test_adjusted_or_keeps_reference_value_and_rejects_risk_ratio_relabel():
    graph = graph_for(['Adjusted odds ratio for previous PPH was 5.43 (95% CI 0.70–43.55).'])
    atom = {'kind': 'effect', 'claim_ids': ['c0'], 'text': '既往PPH的调整OR为5.43（95%CI 0.70–43.55）。',
            'quantity': '5.43', 'effect_type': 'OR', 'unit': 'unknown'}
    assert check_atom(atom, graph)['status'] == 'supported'
    with pytest.raises(StageError):
        check_atom({**atom, 'text': '既往PPH风险增至5.43倍。', 'effect_type': 'RR'}, graph)
    with pytest.raises(StageError):
        check_atom({**atom, 'quantity': '6.43', 'text': '调整OR为6.43。'}, graph)


@pytest.mark.parametrize('label', ['未调整', '未经调整', 'unadjusted '])
def test_faithful_unadjusted_labels_are_not_treated_as_positive_adjustment(label):
    graph = graph_for(['Unadjusted OR 2.0.'])
    assert check_atom({'kind': 'effect', 'claim_ids': ['c0'], 'text': label + 'OR为2.0。',
                       'quantity': '2.0', 'effect_type': 'OR'}, graph)['status'] == 'supported'


def test_five_women_ten_pregnancies_cannot_become_ten_women_or_five_bleeds():
    graph = graph_for(['Five women had 10 pregnancies, with five miscarriages.'])
    atom = {'kind': 'count', 'claim_ids': ['c0'], 'text': '共10次妊娠。',
            'quantity': '10', 'unit': 'pregnancies', 'effect_type': 'unknown'}
    assert check_atom(atom, graph)['status'] == 'supported'
    with pytest.raises(StageError):
        check_atom({**atom, 'text': '共10名女性。', 'unit': 'women'}, graph)
    with pytest.raises(StageError):
        check_atom({**atom, 'text': '共5次出血。', 'quantity': '5', 'unit': 'bleeding_events'}, graph)


def test_global_absence_or_unregistered_numbers_fail_even_with_exact_support():
    graph = graph_for(['Outcome OR 5.8.', 'Methods: adjusted for age and parity.'])
    base = {'kind': 'description', 'claim_ids': ['c0'], 'text': '原文未报告调整变量。',
            'unit': 'unknown', 'quantity': '', 'effect_type': 'unknown'}
    with pytest.raises(StageError):
        check_atom(base, graph)
    with pytest.raises(StageError):
        check_atom({**base, 'text': '显著提升42%，风险比为5.8。'}, graph)


def test_conflicts_must_explicitly_remain_conflicts_not_subgroups():
    graph = graph_for(['Abstract: 20/35 were asymptomatic.', 'Figure: 10/35 were asymptomatic.'])
    atom = {'kind': 'source_conflict', 'claim_ids': ['c0', 'c1'],
            'text': '无症状人数20/35与10/35不一致，尚待澄清。',
            'unit': 'unknown', 'quantity': '', 'effect_type': 'unknown'}
    assert check_atom(atom, graph)['status'] == 'source_conflict'
    with pytest.raises(StageError):
        check_atom({**atom, 'text': '10/35是子组，20/35是总体。'}, graph)
