"""Deterministic, lossless grounded graph merging with digest preconditions."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

def graph_digest(network: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(network, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()

class MergeStageError(ValueError):
    """Raised when a graph delta cannot be merged without data loss."""

def _default_title(graph: dict[str, Any]) -> str:
    facts = {fact.get("claim_id"): fact for fact in graph.get("facts", [])
             if isinstance(fact, dict)}
    titles = [node.get("title") for node in graph.get("nodes", [])
              if isinstance(node, dict) and isinstance(node.get("title"), str)
              and any(cid in facts and not facts[cid].get("topic")
                      for cid in node.get("claim_ids", []))]
    return min(titles) if titles else "证据"

def merge_evidence_graph(
    network: dict[str, Any], delta: dict[str, Any], prev_digest: str | None
) -> dict[str, Any]:
    """Merge facts and sources after CAS and identity-conflict checks."""
    if not isinstance(network, dict) or not isinstance(delta, dict):
        raise MergeStageError("network and delta must be graph objects")
    if prev_digest is not None and graph_digest(network) != prev_digest:
        raise MergeStageError("CAS conflict: network digest mismatch")
    for graph in (network, delta):
        if not isinstance(graph.get("facts"), list) or not isinstance(
                graph.get("sources"), list):
            raise MergeStageError("graph must contain facts and sources lists")

    sources = _merge_sources(network["sources"], delta["sources"])
    facts, merge_log = _merge_facts(network["facts"], delta["facts"])

    try:
        from .knowledge_min import build_evidence_graph
        merged = build_evidence_graph(
            list(facts.values()), list(sources.values()),
            title=_default_title(network),
        )
    except (TypeError, ValueError) as exc:
        raise MergeStageError(f"invalid merged graph: {exc}") from exc
    skipped = sum(item["kind"] == "skipped" for item in merge_log)
    return {
        "graph": merged,
        "latest_digest": graph_digest(merged),
        "counts": {"new_facts": len(facts) - len(network["facts"]),
                   "skipped": skipped, "conflicts": 0},
        "merge_log": merge_log,
    }

def _merge_sources(
    old_sources: list[dict[str, Any]], new_sources: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for index, batch in enumerate((old_sources, new_sources)):
        for source in batch:
            if not isinstance(source, dict) or not isinstance(
                    source.get("source_id"), str):
                raise MergeStageError("invalid source record")
            source_id = source["source_id"]
            old = sources.get(source_id)
            if old is not None:
                keys = ("locator", "text", "content_sha256")
                if any(old.get(key) != source.get(key) for key in keys):
                    raise MergeStageError(
                        f"conflicting source version: {source_id}")
                if index == 0:
                    raise MergeStageError(
                        f"duplicate source_id in network: {source_id}")
            else:
                sources[source_id] = copy.deepcopy(source)
    return sources

def _merge_facts(
    old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    facts: dict[str, dict[str, Any]] = {}
    for fact in old_facts:
        if not isinstance(fact, dict) or not isinstance(
                fact.get("claim_id"), str):
            raise MergeStageError("invalid fact record")
        claim_id = fact["claim_id"]
        if claim_id in facts:
            raise MergeStageError(f"duplicate claim_id: {claim_id}")
        facts[claim_id] = copy.deepcopy(fact)
    log: list[dict[str, str]] = []
    for fact in new_facts:
        if not isinstance(fact, dict) or not isinstance(
                fact.get("claim_id"), str):
            raise MergeStageError("invalid fact record")
        claim_id = fact["claim_id"]
        old = facts.get(claim_id)
        if old is not None:
            immutable = ("text", "citations")
            if any(old.get(key) != fact.get(key) for key in immutable):
                raise MergeStageError(f"conflicting claim_id: {claim_id}")
            log.append({"kind": "skipped", "claim_id": claim_id})
        else:
            facts[claim_id] = copy.deepcopy(fact)
            log.append({"kind": "new_fact", "claim_id": claim_id})
    log.sort(key=lambda item: (item["claim_id"], item["kind"]))
    return facts, log
