"""A large coverage queue must not starve substantive model gaps of search budget."""

from __future__ import annotations

import copy

from research_tool.nine_loop.research import plan_targeted


def finding(kind, index, priority="high", query=None):
    return {"finding_id": f"{kind}:{index}", "priority": priority,
            "description": "missing independent evidence", "claim_ids": [],
            "query": query if query is not None else f"{kind} query {index}"}


def test_large_coverage_queue_reserves_search_opportunities_for_model_gaps():
    inspection = {"findings": [finding("coverage", i) for i in range(110)]
                  + [finding("model", i) for i in range(8)]}
    frozen = copy.deepcopy(inspection)

    queries = plan_targeted(inspection)

    assert len(queries) == 8
    assert any(query.startswith("model") for query in queries)
    assert any(query.startswith("coverage") for query in queries)
    assert inspection == frozen  # selecting searches never closes/downgrades findings


def test_queries_rank_by_priority_before_fairly_alternating_finding_kinds():
    inspection = {"findings": [finding("coverage", 0, "low"), finding("model", 0, "medium"),
                               finding("coverage", 1, "high"), finding("model", 1, "high")]}

    queries = plan_targeted(inspection, max_queries=3)

    assert set(queries[:2]) == {"coverage query 1", "model query 1"}
    assert queries[2] == "model query 0"


def test_repeated_queries_do_not_skip_a_groups_next_distinct_search_turn():
    inspection = {"findings": [finding("model", i, query="model A") for i in range(4)]
                  + [finding("model", 4, query="model B")]
                  + [finding("coverage", i) for i in range(5)]}
    queries = plan_targeted(inspection, max_queries=4)
    assert queries == ["model A", "coverage query 0", "model B", "coverage query 1"]


def test_duplicate_blank_queries_do_not_consume_budget_and_zero_budget_is_empty():
    inspection = {"findings": [finding("coverage", 0, query="  same query  "),
                               finding("model", 1, query="same query"),
                               finding("model", 2, query=" "), finding("model", 3)]}
    assert set(plan_targeted(inspection, max_queries=2)) == {"same query", "model query 3"}
    assert plan_targeted(inspection, max_queries=0) == []
