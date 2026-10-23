"""Reinspecting literal same-source dependencies must preserve gaps without giant LLM replay."""
from research_tool.nine_loop.research import inspect_context_dependencies
from research_tool.tests.test_atomic_grounding import graph_for


def test_same_source_context_inspection_retains_every_prior_finding_and_never_resolves():
    graph = graph_for(['Outcome OR 5.8.', 'Methods: adjusted for age and parity.'])
    previous = {'findings':[{'finding_id':'model:prior','priority':'high','description':'Future risk unknown',
                            'query':'prospective evidence','claim_ids':['c0']}], 'resolutions':[]}
    result = inspect_context_dependencies(graph, previous, minimum_sources=3)
    assert previous['findings'][0] in result['findings']
    assert result['resolutions'] == []
    assert result['context_inspection'] == 'exact_same_source_dependencies_only'
    assert result['scientific_gaps_closed'] == 0
    assert any(f['finding_id'].startswith('coverage:') for f in result['findings'])
