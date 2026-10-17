"""Build a deterministic graph from source-grounded facts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .evidence import verify_span


def _canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _source_map(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(sources, list) or not sources:
        raise ValueError("knowledge requires sources")
    result: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        fields = ("source_id", "locator", "text", "content_sha256")
        if any(not isinstance(source.get(key), str) or not source[key]
               for key in fields):
            raise ValueError("source is missing required fields")
        sid = source["source_id"]
        actual = hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
        if actual != source["content_sha256"]:
            raise ValueError(f"source body hash mismatch: {sid}")
        if "title" in source and not isinstance(source["title"], str):
            raise ValueError(f"source title must be a string: {sid}")
        if sid in result:
            raise ValueError(f"duplicate source_id: {sid}")
        result[sid] = copy.deepcopy(source)
    return result


def _validated_facts(
    facts: list[dict[str, Any]], sources: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    if not isinstance(facts, list) or not facts:
        raise ValueError("knowledge requires at least one fact")
    result: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    for raw in facts:
        if not isinstance(raw, dict):
            raise ValueError("fact must be an object")
        fact = copy.deepcopy(raw)
        cid = fact.get("claim_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError("fact is missing claim_id")
        if cid in claim_ids:
            raise ValueError(f"duplicate claim_id: {cid}")
        claim_ids.add(cid)
        if not isinstance(fact.get("text"), str) or not fact["text"]:
            raise ValueError(f"fact text is missing: {cid}")
        citations = fact.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ValueError(f"fact has no citations: {cid}")
        for span in citations:
            _validate_citation(cid, span, sources)
        result.append(fact)
    result.sort(key=lambda fact: (fact["claim_id"], _canonical_record(fact)))
    return result


def _validate_citation(
    claim_id: str, citation: Any, sources: dict[str, dict[str, Any]]
) -> None:
    if not isinstance(citation, dict):
        raise ValueError(f"invalid citation: {claim_id}")
    source_id = citation.get("source_id")
    source = sources.get(source_id) if isinstance(source_id, str) else None
    if source is None:
        raise ValueError(f"unresolved citation source: {claim_id}")
    if citation.get("locator") != source["locator"]:
        raise ValueError(f"citation locator mismatch: {claim_id}")
    if citation.get("content_sha256") != source["content_sha256"]:
        raise ValueError(f"citation body hash mismatch: {claim_id}")
    if not verify_span(source["text"], citation):
        raise ValueError(f"citation span mismatch: {claim_id}")


def build_evidence_graph(
    facts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    title: str = "证据",
) -> dict[str, Any]:
    """Validate every citation and produce a stable graph."""
    if not isinstance(title, str) or not title:
        raise ValueError("title must be a non-empty string")
    source_map = _source_map(sources)
    normalized = _validated_facts(facts, source_map)
    groups: dict[str, list[str]] = {}
    for fact in normalized:
        topic = fact.get("topic")
        name = topic if isinstance(topic, str) and topic else title
        groups.setdefault(name, []).append(fact["claim_id"])
    nodes = [{"node_id": f"topic:{name}", "title": name,
              "claim_ids": sorted(ids)} for name, ids in sorted(groups.items())]
    ordered_sources = sorted(
        source_map.values(),
        key=lambda source: (source["source_id"], _canonical_record(source)),
    )
    return {"nodes": nodes, "facts": normalized, "sources": ordered_sources,
            "counts": {"nodes": len(nodes), "facts": len(normalized),
                       "sources": len(ordered_sources)}}
