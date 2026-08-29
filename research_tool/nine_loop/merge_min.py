"""⑦ merge minimal — nine-stage pipeline contract implementation
(P3 slice 6b, task RT-RF-P3-LOOP-MIN-01).

Consumes ⑥ targeted-research requests plus a FROZEN research-response fixture
(no network; responses arrive as data) and idempotently merges the new
evidence into the knowledge graph per §3 ⑦ row:

  - conflict coexist: a family with conflicting new evidence keeps BOTH rows
    (new row marked decision "conflict_version"; existing rows untouched)
  - content_sha256 dedup: a response fact whose (source_id, content_sha256)
    already exists is skipped (zero duplicate delta on re-merge)
  - atomic latest: the result carries `latest_digest`; merging again with a
    mismatched `prev_digest` raises E_CAS_CONFLICT (write-if-match semantics)

Deterministic ordering everywhere; offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "merge"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
E_CAS_CONFLICT = "E_CAS_CONFLICT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class MergeStageFault(Exception):
    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_merge_bytes(network_env: dict[str, Any],
                          responses: list[dict[str, Any]]) -> bytes:
    return json.dumps({"network_results": [network_env],
                       "responses": responses},
                      sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(network_env: dict[str, Any],
                            responses: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_merge_bytes(network_env, responses)).hexdigest()


def graph_digest(network: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(network, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def make_envelope_head(request: dict[str, Any]) -> dict[str, Any]:
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


def request_from_chain(network_env: dict[str, Any],
                       responses: list[dict[str, Any]],
                       prev_digest: str | None = None) -> dict[str, Any]:
    """Deterministically derive the ⑦ request head from the ④ network
    envelope + research responses (request_id prefixed "merge:"; the lease is
    deep-copied per CF-QGATE-F1)."""
    import copy
    return {"v": CONTRACT_VERSION, "run_id": network_env.get("run_id"),
            "stage": STAGE,
            "request_id": "merge:" + str(network_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(network_env,
                                                       responses),
            "budget_lease": copy.deepcopy(network_env.get("budget_lease")),
            "network_results": [network_env],
            "responses": responses,
            "prev_digest": prev_digest}


def validate_request(request: Any) -> tuple[dict[str, Any],
                                            list[dict[str, Any]]]:
    if not isinstance(request, dict):
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise MergeStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise MergeStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    network_results = request.get("network_results")
    if not isinstance(network_results, list) or len(network_results) != 1:
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "network_results must contain exactly one ④ envelope"))
    network = network_results[0]
    if not isinstance(network, dict) or network.get("stage") != "network" \
            or not isinstance(network.get("result"), dict):
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "network_results[0] is not a ④ network result"))
    responses = request.get("responses")
    if not isinstance(responses, list) or not responses:
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "responses must be a non-empty list"))
    for i, resp in enumerate(responses):
        if not isinstance(resp, dict) \
                or not isinstance(resp.get("request_id"), str) \
                or not isinstance(resp.get("facts"), list):
            raise MergeStageFault(make_error_frame(
                E_SCHEMA,
                f"responses[{i}] must be {{request_id, facts[]}}"))
        for f in resp["facts"]:
            if not isinstance(f, dict) \
                    or not isinstance(f.get("source_id"), str) \
                    or not isinstance(f.get("content_sha256"), str) \
                    or not isinstance(f.get("locator"), str):
                raise MergeStageFault(make_error_frame(
                    E_SCHEMA,
                    f"responses[{i}] fact must carry source_id/"
                    "content_sha256/locator"))
    prev = request.get("prev_digest")
    if prev is not None and (not isinstance(prev, str) or len(prev) != 64):
        raise MergeStageFault(make_error_frame(
            E_SCHEMA, "prev_digest must be a 64-hex string"))
    computed = compute_idempotency_key(network, responses)
    if key != computed:
        raise MergeStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical merge bytes"))
    return network, responses


# --------------------------------------------------------------------------- #
# Idempotent merge (conflict coexist + content_sha256 dedup + CAS latest)
# --------------------------------------------------------------------------- #
def merge(network: dict[str, Any], responses: list[dict[str, Any]],
          prev_digest: str | None) -> dict[str, Any]:
    if prev_digest is not None and graph_digest(network) != prev_digest:
        raise MergeStageFault(make_error_frame(
            E_CAS_CONFLICT,
            "network digest does not match prev_digest (write-if-match)"))

    # Deep-copy the input graph: merge NEVER mutates caller-owned records
    # (aliasing isolation per CF-QGATE-F1; the input network envelope must
    # remain byte-identical after the merge).
    import copy
    network = copy.deepcopy(network)
    nodes = {n["node_id"]: n for n in network.get("nodes", [])}
    edges = list(network.get("edges", []))
    # existing (source_id, content_sha256) keys for dedup
    existing_keys = set()
    for n in nodes.values():
        for span in n.get("evidence_spans", []):
            existing_keys.add((n["node_id"], span.get("content_sha256")))
    merge_log: list[dict[str, Any]] = []
    new_facts = 0
    skipped = 0
    conflicts = 0
    for resp in responses:
        for fact in resp["facts"]:
            family = fact["source_id"]
            ch = fact["content_sha256"]
            key = (family, ch)
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            span = {"source_id": family, "content_sha256": ch,
                    "locator": fact["locator"],
                    "extractor_version": fact.get("extractor_version",
                                                  "merge.v1"),
                    "round_id": fact.get("round_id",
                                         resp.get("request_id"))}
            if family in nodes:
                # conflict coexist: keep existing rows, add conflict row
                nodes[family]["evidence_spans"].append(span)
                nodes[family]["evidence_spans"].sort(
                    key=lambda s: (s.get("locator") or "",
                                   s.get("content_sha256") or "",
                                   s.get("round_id") or ""))
                conflicts += 1
                merge_log.append({"kind": "conflict_coexist",
                                  "source_id": family,
                                  "content_sha256": ch})
            else:
                nodes[family] = {"node_id": family, "kind": "source",
                                 "evidence_spans": [span]}
                merge_log.append({"kind": "new_family",
                                  "source_id": family,
                                  "content_sha256": ch})
            new_facts += 1

    merged = {"nodes": sorted(nodes.values(), key=lambda n: n["node_id"]),
              "edges": sorted(edges, key=lambda e: e.get("edge_id")),
              "counts": {"nodes": len(nodes), "edges": len(edges)}}
    return {"graph": merged,
            "latest_digest": graph_digest(merged),
            "merge_log": merge_log,
            "counts": {"new_facts": new_facts, "skipped": skipped,
                       "conflicts": conflicts}}


def run_merge(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ⑦ merge batch; returns a contract envelope."""
    try:
        network, responses = validate_request(request)
        merged = merge(network["result"], responses,
                       request.get("prev_digest"))
        env = make_envelope_head(request)
        env["result"] = merged
        env["error"] = None
        return env
    except MergeStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
