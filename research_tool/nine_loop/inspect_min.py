"""⑤ inspect minimal — nine-stage pipeline contract implementation
(P3 slice 3, task RT-RF-P3-INSPECT-MIN-01).

Consumes ④ knowledge-network Result envelopes (frozen JSONL) and applies the
frozen §3 ⑤-row detection rules to produce a GapReport:
  contradiction findings — every `same_bytes` edge is a cross-family
                           same-content relation that requires resolution
  gap findings           — deterministic structural gaps:
                           `orphan_node`   (family with no same_bytes edge)
                           `span_incomplete` (evidence span content_sha256
                                              is null)

Determinism: findings sorted by (priority_rank, finding_id); every list
explicitly sorted; identical input bytes ⇒ byte-identical output. Failure
semantics: typed error frame, result=None (partial-output forbidden).
Offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "inspect"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

# Frozen detection-rule priorities (lower rank = higher priority)
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class InspectStageFault(Exception):
    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_network_bytes(network_envelopes: list[dict[str, Any]]) -> bytes:
    return json.dumps(network_envelopes, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(
        network_envelopes: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_network_bytes(network_envelopes)).hexdigest()


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


def validate_request(request: Any) -> list[dict[str, Any]]:
    if not isinstance(request, dict):
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise InspectStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise InspectStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    envelopes = request.get("network_results")
    if not isinstance(envelopes, list) or not envelopes:
        raise InspectStageFault(make_error_frame(
            E_SCHEMA, "network_results must be a non-empty list"))
    for i, env in enumerate(envelopes):
        if not isinstance(env, dict) or env.get("stage") != "network":
            raise InspectStageFault(make_error_frame(
                E_SCHEMA, f"network_results[{i}] is not a ④ network result"))
        if env.get("error") is not None \
                or not isinstance(env.get("result"), dict):
            raise InspectStageFault(make_error_frame(
                E_SCHEMA, f"network_results[{i}] carries no success result"))
        result = env["result"]
        if not isinstance(result.get("nodes"), list) \
                or not isinstance(result.get("edges"), list):
            raise InspectStageFault(make_error_frame(
                E_SCHEMA,
                f"network_results[{i}].result.nodes/edges must be lists"))
    computed = compute_idempotency_key(envelopes)
    if key != computed:
        raise InspectStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical network_results bytes"))
    return envelopes


def request_from_network(network_env: dict[str, Any]) -> dict[str, Any]:
    """Deterministically derive the ⑤ request head from a ④ network envelope."""
    envelopes = [network_env]
    return {"v": CONTRACT_VERSION, "run_id": network_env.get("run_id"),
            "stage": STAGE,
            "request_id": "inspect:" + str(network_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(envelopes),
            "budget_lease": network_env.get("budget_lease"),
            "network_results": envelopes}


# --------------------------------------------------------------------------- #
# Frozen detection rules (§3 ⑤ row)
# --------------------------------------------------------------------------- #
def detect(network: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    # Rule C1: every same_bytes edge is a cross-family same-content
    # contradiction candidate (two families claim identical bytes).
    for edge in network.get("edges", []):
        if edge.get("type") != "same_bytes":
            continue
        findings.append({
            "finding_id": f"contradiction:{edge['edge_id']}",
            "type": "contradiction",
            "code": "same_bytes_cross_family",
            "priority": "high",
            "source": edge["source"],
            "target": edge["target"],
            "content_sha256": edge.get("content_sha256"),
            "evidence": {"locators": edge.get("locators", [])},
        })

    degree: dict[str, int] = {n["node_id"]: 0 for n in network.get("nodes", [])}
    for edge in network.get("edges", []):
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1

    # Rule G1: orphan family (no same_bytes relation) — isolated knowledge.
    for node in sorted(network.get("nodes", []),
                       key=lambda n: n["node_id"]):
        if degree.get(node["node_id"], 0) == 0:
            findings.append({
                "finding_id": f"gap:orphan_node:{node['node_id']}",
                "type": "gap",
                "code": "orphan_node",
                "priority": "medium",
                "source": node["node_id"],
                "evidence": {"evidence_span_count":
                             len(node.get("evidence_spans", []))},
            })

    # Rule G2: incomplete evidence span (content hash null) — provenance gap.
    for node in sorted(network.get("nodes", []),
                       key=lambda n: n["node_id"]):
        for span in node.get("evidence_spans", []):
            if span.get("content_sha256") is None:
                findings.append({
                    "finding_id": (f"gap:span_incomplete:{node['node_id']}:"
                                   f"{span.get('locator')}"),
                    "type": "gap",
                    "code": "span_incomplete",
                    "priority": "low",
                    "source": node["node_id"],
                    "evidence": {"locator": span.get("locator"),
                                 "round_id": span.get("round_id")},
                })

    findings.sort(key=lambda f: (_PRIORITY_RANK[f["priority"]],
                                 f["finding_id"]))
    return findings


# --------------------------------------------------------------------------- #
# Stage execution
# --------------------------------------------------------------------------- #
def run_inspect(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ⑤ inspect batch; returns a contract envelope.

    Success: envelope["result"] = {findings[], counts{}} with deterministic
    ordering. Failure: envelope["error"] = typed frame and
    envelope["result"] = None (partial-output forbidden).
    """
    try:
        envelopes = validate_request(request)
        network_results = []
        findings = []
        for env in envelopes:
            network = env["result"]
            findings.extend(detect(network))
            network_results.append({
                "request_id": env.get("request_id"),
                "network_digest": hashlib.sha256(
                    canonical_network_bytes([env])).hexdigest(),
            })
        counts = {"total": len(findings)}
        for f in findings:
            counts[f["priority"]] = counts.get(f["priority"], 0) + 1
        env_out = make_envelope_head(request)
        env_out["result"] = {"findings": findings,
                             "networks_inspected": network_results,
                             "counts": counts}
        env_out["error"] = None
        return env_out
    except InspectStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
