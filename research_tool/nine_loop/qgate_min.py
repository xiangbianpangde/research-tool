"""⑧ quality-gate minimal — nine-stage pipeline contract implementation
(P3 slice 4, task RT-RF-P3-QGATE-MIN-01).

Consumes ⑤ inspect Result envelopes (frozen JSONL) and evaluates the gate
decision per docs/plan/NINE-STAGE-CONTRACTS.md §3 ⑧ row:
  GateDecision{verdict: CONTINUE | STOP_SUCCESS | STOP_BUDGET | STOP_ERROR |
               STOP_CANCELLED, reasons[]} — stop precedence aligned with the
               protocol STOP_* semantics (§6 of the nine-stage contracts).

Frozen deterministic decision tree (evaluated in order):
  1. structural validation            → typed frames (E_VERSION/E_SCHEMA/
                                        E_LEASE_INVALID/E_IDEMPOTENCY_CONFLICT)
  2. upstream ⑤ error envelope        → verdict STOP_ERROR (highest precedence
                                        among evaluable inputs)
  3. lease exhausted (wall/tokens/
     cost at zero)                    → typed frame E_BUDGET_EXHAUSTED
  4. high findings > max_high         → CONTINUE (targeted-research loop)
  5. total findings > max_total       → CONTINUE (degraded note when only low)
  6. otherwise                        → STOP_SUCCESS (degraded note when only
                                        low findings remain)

Threshold values are PROPOSED (marked at the call boundary); the threshold
STRUCTURE is frozen. Determinism: explicit sorting everywhere; identical
input bytes ⇒ byte-identical output. Offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "gate"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
E_BUDGET_EXHAUSTED = "E_BUDGET_EXHAUSTED"

VERDICTS = ("CONTINUE", "STOP_SUCCESS", "STOP_BUDGET", "STOP_ERROR",
            "STOP_CANCELLED")

# PROPOSED threshold values (frozen STRUCTURE; numbers may be re-frozen at the
# P4 gate via an append-style revision — never edited in place).
PROPOSED_THRESHOLDS = {"max_high_findings": 0, "max_total_findings": 10}

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class GateStageFault(Exception):
    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_inspect_bytes(inspect_envelopes: list[dict[str, Any]]) -> bytes:
    return json.dumps(inspect_envelopes, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(inspect_envelopes: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_inspect_bytes(inspect_envelopes)).hexdigest()


def make_envelope_head(request: dict[str, Any]) -> dict[str, Any]:
    return {"v": CONTRACT_VERSION, "run_id": request.get("run_id"),
            "stage": STAGE, "request_id": request.get("request_id"),
            "idempotency_key": request.get("idempotency_key"),
            "budget_lease": request.get("budget_lease")}


def _decision(request: dict[str, Any], verdict: str,
              reasons: list[str]) -> dict[str, Any]:
    env = make_envelope_head(request)
    env["result"] = {"verdict": verdict, "reasons": sorted(reasons),
                     "thresholds": request.get("thresholds")}
    env["error"] = None
    return env


def _fail(request: dict[str, Any], code: str, safe_message: str,
          retryable: bool = False) -> dict[str, Any]:
    env = make_envelope_head(request)
    env["result"] = None
    env["error"] = make_error_frame(code, safe_message, retryable)
    return env


def validate_request(request: Any) -> tuple[list[dict[str, Any]], dict]:
    if not isinstance(request, dict):
        raise GateStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise GateStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise GateStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise GateStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise GateStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise GateStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise GateStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    thresholds = request.get("thresholds")
    if not isinstance(thresholds, dict) \
            or set(thresholds) != {"max_high_findings", "max_total_findings"}:
        raise GateStageFault(make_error_frame(
            E_SCHEMA,
            "thresholds must be {max_high_findings, max_total_findings}"))
    for f in ("max_high_findings", "max_total_findings"):
        if type(thresholds.get(f)) is not int or thresholds[f] < 0:
            raise GateStageFault(make_error_frame(
                E_SCHEMA, f"thresholds.{f} must be a non-negative int"))
    envelopes = request.get("inspect_results")
    if not isinstance(envelopes, list) or not envelopes:
        raise GateStageFault(make_error_frame(
            E_SCHEMA, "inspect_results must be a non-empty list"))
    for i, env in enumerate(envelopes):
        if not isinstance(env, dict) or env.get("stage") != "inspect":
            raise GateStageFault(make_error_frame(
                E_SCHEMA, f"inspect_results[{i}] is not a ⑤ inspect result"))
        if not isinstance(env.get("result"), dict) \
                or not isinstance(env["result"].get("findings"), list):
            raise GateStageFault(make_error_frame(
                E_SCHEMA,
                f"inspect_results[{i}].result.findings must be a list"))
    computed = compute_idempotency_key(envelopes)
    if key != computed:
        raise GateStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical inspect_results bytes"))
    return envelopes, thresholds


def request_from_inspect(inspect_env: dict[str, Any],
                         thresholds: dict[str, int] | None = None) \
        -> dict[str, Any]:
    """Deterministically derive the ⑧ request head from a ⑤ inspect envelope
    (request_id prefixed "gate:"; PROPOSED thresholds by default).

    The budget lease is deep-copied into the head so that callers adjusting
    the lease can never mutate the consumed ⑤ record (aliasing isolation)."""
    import copy
    envelopes = [inspect_env]
    return {"v": CONTRACT_VERSION, "run_id": inspect_env.get("run_id"),
            "stage": STAGE,
            "request_id": "gate:" + str(inspect_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(envelopes),
            "budget_lease": copy.deepcopy(
                inspect_env.get("budget_lease")),
            "thresholds": dict(PROPOSED_THRESHOLDS if thresholds is None
                               else thresholds),
            "inspect_results": envelopes}


# --------------------------------------------------------------------------- #
# Gate evaluation (frozen decision tree; stop precedence per contract §6)
# --------------------------------------------------------------------------- #
def evaluate(request: dict[str, Any], envelopes: list[dict[str, Any]],
             lease: dict[str, Any], thresholds: dict[str, int]) -> dict[str, Any]:
    # 2. upstream ⑤ error envelope → STOP_ERROR (precedence over budget)
    upstream_errors = []
    for env in envelopes:
        err = env.get("error")
        if err is not None:
            upstream_errors.append(
                f"upstream inspect error {err.get('code')}")
    if upstream_errors:
        return _decision(request, "STOP_ERROR",
                         ["upstream inspect failure"] + upstream_errors)

    # 3. lease exhausted at gate time → typed frame (gate cannot evaluate)
    if lease.get("wall_s_max", 0) <= 0 or lease.get("tokens_max", 0) <= 0 \
            or lease.get("cost_max", 0) <= 0:
        raise GateStageFault(make_error_frame(
            E_BUDGET_EXHAUSTED,
            "budget lease exhausted; gate cannot evaluate"))

    findings = []
    for env in envelopes:
        findings.extend(env["result"].get("findings", []))
    high = [f for f in findings if f.get("priority") == "high"]
    low = [f for f in findings if f.get("priority") == "low"]

    # 4. unresolved high (contradiction) findings → targeted-research loop
    if len(high) > thresholds["max_high_findings"]:
        return _decision(request, "CONTINUE", [
            "high-priority findings exceed max_high_findings",
            "route to ⑥ targeted-research"])

    # 5. total findings above threshold → CONTINUE (degraded when only low)
    if len(findings) > thresholds["max_total_findings"]:
        reasons = ["total findings exceed max_total_findings"]
        if not high and low:
            reasons.append("degraded: only low-priority findings remain")
        return _decision(request, "CONTINUE", reasons)

    # 6. quality met (degraded note when low findings remain)
    reasons = ["quality thresholds met"]
    if low:
        reasons.append("degraded: low-priority findings remain")
    return _decision(request, "STOP_SUCCESS", reasons)


def run_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ⑧ gate evaluation; returns a contract envelope.

    Success: envelope["result"] = {verdict, reasons[], thresholds}. Lease
    exhaustion raises the typed E_BUDGET_EXHAUSTED frame (result=None).
    """
    try:
        envelopes, thresholds = validate_request(request)
        return evaluate(request, envelopes, request["budget_lease"],
                        thresholds)
    except GateStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
