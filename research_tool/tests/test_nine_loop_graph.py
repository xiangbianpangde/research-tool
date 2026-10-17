"""Grounded graph construction and merge regressions."""

import copy
import hashlib

import pytest

from research_tool.nine_loop.knowledge_min import build_evidence_graph
from research_tool.nine_loop.merge_min import graph_digest, merge_evidence_graph


def graph_input(letter="a"):
    text = f"Source {letter} reports a measured result."
    source = {"source_id": letter, "locator": f"https://example.test/{letter}",
              "title": letter, "text": text,
              "content_sha256": hashlib.sha256(text.encode()).hexdigest()}
    fact = {"claim_id": letter, "text": text, "topic": "results",
            "citations": [{"source_id": letter,
                           "locator": source["locator"],
                           "content_sha256": source["content_sha256"],
                           "start_offset": 0, "end_offset": len(text),
                           "text": text}]}
    return fact, source


def test_graph_requires_real_body_span_and_keeps_input_immutable():
    fact, source = graph_input()
    original = copy.deepcopy((fact, source))
    graph = build_evidence_graph([fact], [source])
    assert graph["facts"] == [fact]
    assert graph["sources"] == [source]
    assert (fact, source) == original
    fact["citations"][0]["end_offset"] -= 1
    with pytest.raises(ValueError):
        build_evidence_graph([fact], [source])


def test_merge_is_idempotent_and_rejects_stale_digest():
    fact, source = graph_input()
    base = build_evidence_graph([fact], [source])
    delta_fact, delta_source = graph_input("b")
    delta = build_evidence_graph([delta_fact], [delta_source])
    frozen = copy.deepcopy(base)
    first = merge_evidence_graph(base, delta, graph_digest(base))
    second = merge_evidence_graph(first["graph"], delta, first["latest_digest"])
    assert first["counts"]["new_facts"] == 1
    assert second["counts"]["new_facts"] == 0
    assert second["graph"] == first["graph"]
    assert base == frozen
    with pytest.raises(ValueError, match="CAS"):
        merge_evidence_graph(base, delta, "0" * 64)


def test_unresolved_source_and_conflicting_version_are_rejected():
    fact, source = graph_input()
    with pytest.raises(ValueError):
        build_evidence_graph([fact], [])
    graph = build_evidence_graph([fact], [source])
    other = {**source, "text": "changed"}
    with pytest.raises(ValueError, match="conflict"):
        merge_evidence_graph(graph, {"sources": [other], "facts": []}, graph_digest(graph))


def test_every_citation_must_resolve_to_its_exact_source_and_span():
    fact, source = graph_input()
    bad_citations = [
        {**fact["citations"][0], "source_id": "missing"},
        {**fact["citations"][0], "locator": "https://example.test/elsewhere"},
        {**fact["citations"][0], "content_sha256": "0" * 64},
        {**fact["citations"][0], "start_offset": 1},
    ]
    for citation in bad_citations:
        mixed = copy.deepcopy(fact)
        mixed["citations"].append(citation)
        with pytest.raises(ValueError):
            build_evidence_graph([mixed], [source])


def test_duplicate_source_ids_are_rejected_without_overwriting():
    fact, source = graph_input()
    duplicate = {**source, "text": "different body",
                 "content_sha256": hashlib.sha256(
                     b"different body").hexdigest()}
    with pytest.raises(ValueError, match="duplicate source_id"):
        build_evidence_graph([fact], [source, duplicate])


def test_same_immutable_fact_with_different_topic_is_not_a_merge_conflict():
    fact, source = graph_input()
    graph = build_evidence_graph([fact], [source])
    changed = copy.deepcopy(fact)
    changed["topic"] = "alternative classification"
    result = merge_evidence_graph(graph, {"facts": [changed], "sources": [source]}, graph_digest(graph))
    assert result["graph"]["facts"] == [fact]
    assert result["counts"]["skipped"] == 1
