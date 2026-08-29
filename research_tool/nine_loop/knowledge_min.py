"""④ knowledge-network minimal — nine-stage pipeline contract implementation
(P3 slice 2, task RT-RF-P3-KNOWLEDGE-MIN-01).

Consumes ① collect Result envelopes (frozen JSONL) and builds the minimal
in-memory network per docs/plan/NINE-STAGE-CONTRACTS.md §3 ④ row:
  nodes  — one per unique source_id family, carrying the sorted evidence
           spans of every identity that resolved into the family
  edges  — `same_bytes` relations between distinct source families that share
           a non-null content_sha256 (mirrors the core's
           same_bytes_distinct_id semantics; rows are never dropped)

Determinism: every output list is explicitly sorted (never dict order);
identical input bytes ⇒ byte-identical output. Failure semantics: typed
error frame per contract §4, result=None (partial-output forbidden).
Offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "network"
CONTRACT_VERSION = 1

# Shared error codes (NINE-STAGE-CONTRACTS §4 registry)
E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


# --------------------------------------------------------------------------- #
# Typed error frame (contract §2)
# --------------------------------------------------------------------------- #
def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class KnowledgeStageFault(Exception):
    """Internal typed fault; carries the contract error frame only."""

    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


# --------------------------------------------------------------------------- #
# Envelope helpers
# --------------------------------------------------------------------------- #
def canonical_records_bytes(collect_results: list[dict[str, Any]]) -> bytes:
    """Deterministic bytes over the consumed ① records (idempotency basis)."""
    return json.dumps(collect_results, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(collect_results: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_records_bytes(collect_results)).hexdigest()


def make_envelope_head(request: dict[str, Any]) -> dict[str, Any]:
    """Echo the provided envelope head; validation failures may reference
    requests with missing fields, so every field uses a safe .get()."""
    return {"v": CONTRACT_VERSION, "run_id": request.get("run_id"),
            "stage": STAGE, "request_id": request.get("request_id"),
            "idempotency_key": request.get("idempotency_key"),
            "budget_lease": request.get("budget_lease")}


def _fail(request: dict[str, Any], code: str, safe_message: str,
          retryable: bool = False) -> dict[str, Any]:
    env = make_envelope_head(request)
    env["result"] = None
    env["error"] = make_error_frame(code, safe_message, retryable)
    return env


# --------------------------------------------------------------------------- #
# Request validation (contract §2; explicit frames)
# --------------------------------------------------------------------------- #
def validate_request(request: Any) -> list[dict[str, Any]]:
    if not isinstance(request, dict):
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise KnowledgeStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise KnowledgeStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    records = request.get("collect_results")
    if not isinstance(records, list) or not records:
        raise KnowledgeStageFault(make_error_frame(
            E_SCHEMA, "collect_results must be a non-empty list"))
    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or rec.get("stage") != "collect":
            raise KnowledgeStageFault(make_error_frame(
                E_SCHEMA, f"collect_results[{i}] is not a ① collect result"))
        if rec.get("error") is not None or not isinstance(
                rec.get("result"), dict):
            raise KnowledgeStageFault(make_error_frame(
                E_SCHEMA, f"collect_results[{i}] carries no success result"))
        result = rec["result"]
        if not isinstance(result.get("seeds"), list) \
                or not result["seeds"]:
            raise KnowledgeStageFault(make_error_frame(
                E_SCHEMA, f"collect_results[{i}].result.seeds empty"))
    computed = compute_idempotency_key(records)
    if key != computed:
        raise KnowledgeStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical collect_results bytes"))
    return records


# --------------------------------------------------------------------------- #
# Graph construction (§3 ④ row: minimal nodes/edges + evidence spans)
# --------------------------------------------------------------------------- #
def build_network(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic minimal network from ① collect seed records.

    nodes: one per unique source_id family; evidence spans sorted.
    edges: same_bytes relations between distinct families sharing a non-null
           content_sha256; sorted by (source, target).
    """
    spans_by_family: dict[str, list[dict[str, Any]]] = {}
    hash_owners: dict[str, set[str]] = {}
    locators_by_hash_family: dict[tuple[str, str], set[str]] = {}

    for rec in records:
        for seed in rec["result"]["seeds"]:
            span = seed["evidence_span"]
            family = span["source_id"]
            spans_by_family.setdefault(family, []).append(span)
            ch = span.get("content_sha256")
            if ch:
                hash_owners.setdefault(ch, set()).add(family)
                locators_by_hash_family.setdefault(
                    (ch, family), set()).add(span["locator"])

    nodes = []
    for family in sorted(spans_by_family):
        spans = sorted(
            spans_by_family[family],
            key=lambda s: (s.get("locator") or "", s.get("content_sha256")
                           or "", s.get("round_id") or ""))
        nodes.append({"node_id": family, "kind": "source",
                      "evidence_spans": spans})

    edges = []
    for ch in sorted(hash_owners):
        families = sorted(hash_owners[ch])
        for i in range(len(families)):
            for j in range(i + 1, len(families)):
                a, b = families[i], families[j]
                locators = sorted(
                    locators_by_hash_family.get((ch, a), set())
                    | locators_by_hash_family.get((ch, b), set()))
                edges.append({
                    "edge_id": f"same_bytes:{a}:{b}",
                    "type": "same_bytes",
                    "source": a,
                    "target": b,
                    "content_sha256": ch,
                    "locators": locators,
                })

    return {"nodes": nodes, "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges)}}


# --------------------------------------------------------------------------- #
# Stage execution
# --------------------------------------------------------------------------- #
def request_from_collect(collect_env: dict[str, Any]) -> dict[str, Any]:
    """Deterministically derive the ④ request head from a ① collect envelope
    (same run_id; request_id prefixed "network:"; same lease; idempotency
    key recomputed over the consumed records)."""
    records = [collect_env]
    return {"v": CONTRACT_VERSION, "run_id": collect_env.get("run_id"),
            "stage": STAGE,
            "request_id": "network:" + str(collect_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(records),
            "budget_lease": collect_env.get("budget_lease"),
            "collect_results": records}


def run_network(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ④ knowledge-network batch; returns a contract envelope.

    Success: envelope["result"] = {nodes[], edges[], counts{}} with fully
    deterministic ordering. Failure: envelope["error"] = typed frame and
    envelope["result"] = None (partial-output forbidden).
    """
    try:
        records = validate_request(request)
        network = build_network(records)
        env = make_envelope_head(request)
        env["result"] = network
        env["error"] = None
        return env
    except KnowledgeStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
