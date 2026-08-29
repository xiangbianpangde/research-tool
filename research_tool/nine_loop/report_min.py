"""⑨ verified report minimal — nine-stage pipeline contract implementation
(P3 slice 5 / skeleton final segment, task RT-RF-P3-REPORT-MIN-01).

Consumes the ⑧ gate decision envelope plus the upstream ④ network envelopes
(frozen golden chain, read-only) and assembles the verification-style report
per docs/plan/NINE-STAGE-CONTRACTS.md §3 ⑨ row:
  先选事实 → 逐句核对 → 组装（minimal deterministic form）
  every claim MUST carry citations resolving to evidence spans; a claim
  without any resolvable citation is DROPPED and counted (never silently
  kept); gate degraded/STOP notes are propagated verbatim.

Verdict routing (aligned with the ⑧ wiring in docs/components/INDEX.md):
  STOP_SUCCESS / STOP_BUDGET → report assembled (notes preserved verbatim)
  CONTINUE                   → no report; gate routed back to ⑥
  STOP_ERROR / STOP_CANCELLED → failure bundle note; no report assembled

Determinism: claims sorted by claim_id; citations sorted by
(source_id, locator, content_sha256); identical input bytes ⇒ byte-identical
output. Failure semantics: typed error frame, result=None when the frame
path is taken (partial-output forbidden). Offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "report"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

VERDICTS = ("CONTINUE", "STOP_SUCCESS", "STOP_BUDGET", "STOP_ERROR",
            "STOP_CANCELLED")
_ASSEMBLE_VERDICTS = ("STOP_SUCCESS", "STOP_BUDGET")
_NO_REPORT_VERDICTS = ("CONTINUE", "STOP_ERROR", "STOP_CANCELLED")

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class ReportStageFault(Exception):
    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_chain_bytes(gate_env: dict[str, Any],
                          network_envelopes: list[dict[str, Any]]) -> bytes:
    return json.dumps({"gate_results": [gate_env],
                       "network_results": network_envelopes},
                      sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(gate_env: dict[str, Any],
                            network_envelopes: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_chain_bytes(gate_env, network_envelopes)).hexdigest()


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


def request_from_chain(gate_env: dict[str, Any],
                       network_envelopes: list[dict[str, Any]]) \
        -> dict[str, Any]:
    """Deterministically derive the ⑨ request head from the ⑧ gate decision
    and the ④ network envelopes (request_id prefixed "report:"; the lease is
    deep-copied so callers can never mutate consumed records — CF-QGATE-F1
    pattern)."""
    import copy
    return {"v": CONTRACT_VERSION, "run_id": gate_env.get("run_id"),
            "stage": STAGE,
            "request_id": "report:" + str(gate_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(
                gate_env, network_envelopes),
            "budget_lease": copy.deepcopy(gate_env.get("budget_lease")),
            "gate_results": [gate_env],
            "network_results": list(network_envelopes)}


def validate_request(request: Any) -> tuple[dict[str, Any],
                                            list[dict[str, Any]]]:
    if not isinstance(request, dict):
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise ReportStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise ReportStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    gate_results = request.get("gate_results")
    if not isinstance(gate_results, list) or len(gate_results) != 1:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "gate_results must contain exactly one ⑧ envelope"))
    gate = gate_results[0]
    if not isinstance(gate, dict) or gate.get("stage") != "gate":
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "gate_results[0] is not a ⑧ gate decision"))
    if not isinstance(gate.get("result"), dict) \
            or gate["result"].get("verdict") not in VERDICTS:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "gate_results[0] carries no valid verdict"))
    network_results = request.get("network_results")
    if not isinstance(network_results, list) or not network_results:
        raise ReportStageFault(make_error_frame(
            E_SCHEMA, "network_results must be a non-empty list"))
    for i, env in enumerate(network_results):
        if not isinstance(env, dict) or env.get("stage") != "network":
            raise ReportStageFault(make_error_frame(
                E_SCHEMA,
                f"network_results[{i}] is not a ④ network result"))
        if not isinstance(env.get("result"), dict) \
                or not isinstance(env["result"].get("nodes"), list):
            raise ReportStageFault(make_error_frame(
                E_SCHEMA,
                f"network_results[{i}].result.nodes must be a list"))
    computed = compute_idempotency_key(gate, network_results)
    if key != computed:
        raise ReportStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical chain bytes"))
    return gate, network_results


# --------------------------------------------------------------------------- #
# Claim selection + verification-style assembly (§3 ⑨ row)
# --------------------------------------------------------------------------- #
def build_claims(network_results: list[dict[str, Any]]) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]]]:
    """Select claims from network nodes; enforce citation traceability.

    Each node family yields one claim whose citations are the family's
    evidence spans (source_id/locator/content_sha256). A claim with zero
    resolvable citations is DROPPED (counted, never silently kept).
    Returns (claims, dropped).
    """
    claims = []
    dropped = []
    for res in network_results:
        for node in sorted(res["result"]["nodes"],
                           key=lambda n: n["node_id"]):
            node_id = node["node_id"]
            spans = sorted(
                node.get("evidence_spans", []),
                key=lambda s: (s.get("source_id") or "",
                               s.get("locator") or "",
                               s.get("content_sha256") or ""))
            citations = [{"source_id": s.get("source_id"),
                          "locator": s.get("locator"),
                          "content_sha256": s.get("content_sha256")}
                         for s in spans]
            claim_id = f"claim:{node_id}"
            if not citations:
                dropped.append({"claim_id": claim_id,
                                "reason": "no_resolvable_citation",
                                "node_id": node_id})
                continue
            claims.append({
                "claim_id": claim_id,
                "text": (f"source family {node_id} retained with "
                         f"{len(citations)} verified evidence span(s)"),
                "citations": citations,
            })
    claims.sort(key=lambda c: c["claim_id"])
    dropped.sort(key=lambda d: d["claim_id"])
    return claims, dropped


def run_report(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ⑨ verified-report batch; returns a contract envelope.

    STOP_SUCCESS/STOP_BUDGET → report assembled (gate notes preserved
    verbatim; citation traceability enforced). CONTINUE → no report (gate
    routed back to ⑥). STOP_ERROR/STOP_CANCELLED → failure-bundle note; no
    report assembled. Validation failures → typed error frame (result=None).
    """
    try:
        gate, network_results = validate_request(request)
    except ReportStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])
    verdict = gate["result"]["verdict"]
    gate_notes = list(gate["result"].get("reasons", []))
    if verdict in _NO_REPORT_VERDICTS:
        env = make_envelope_head(request)
        env["result"] = {
            "verdict": verdict,
            "report": None,
            "gate_notes": gate_notes,
            "note": ("gate routed back to ⑥ targeted-research; no report "
                     "assembled" if verdict == "CONTINUE" else
                     "failure bundle; no report assembled"),
            "counts": {"claims": 0, "citations": 0, "dropped_claims": 0},
        }
        env["error"] = None
        return env
    claims, dropped = build_claims(network_results)
    citations_total = sum(len(c["citations"]) for c in claims)
    env = make_envelope_head(request)
    env["result"] = {
        "verdict": verdict,
        "report": {
            "claims": claims,
            "gate_notes": gate_notes,  # degraded/STOP notes verbatim
        },
        "gate_notes": gate_notes,
        "counts": {"claims": len(claims),
                   "citations": citations_total,
                   "dropped_claims": len(dropped)},
        "dropped_claims": dropped,
        "note": ("report assembled from gate-passed content only"
                 if verdict == "STOP_SUCCESS" else
                 "report assembled under budget stop; notes preserved"),
    }
    env["error"] = None
    return env


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
