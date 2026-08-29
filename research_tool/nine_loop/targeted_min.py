"""⑥ targeted-research minimal — nine-stage pipeline contract implementation
(P3 slice 6a, task RT-RF-P3-LOOP-MIN-01).

Consumes ⑤ inspect GapReport findings and DETERMINISTICALLY generates the
targeted-research Request list (only request generation; this module makes
NO network syscall of any kind — static test asserts no network imports and
no socket usage). Each finding yields exactly one research request; requests
are sorted by query_id (zero dict-order dependence).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "targeted"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class TargetedStageFault(Exception):
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


def _fail(request: dict[str, Any], code: str, safe_message: str,
          retryable: bool = False) -> dict[str, Any]:
    env = make_envelope_head(request)
    env["result"] = None
    env["error"] = make_error_frame(code, safe_message, retryable)
    return env


def request_from_inspect(inspect_env: dict[str, Any]) -> dict[str, Any]:
    import copy
    envelopes = [inspect_env]
    return {"v": CONTRACT_VERSION, "run_id": inspect_env.get("run_id"),
            "stage": STAGE,
            "request_id": "targeted:" + str(inspect_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(envelopes),
            "budget_lease": copy.deepcopy(inspect_env.get("budget_lease")),
            "inspect_results": envelopes}


def validate_request(request: Any) -> list[dict[str, Any]]:
    if not isinstance(request, dict):
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise TargetedStageFault(make_error_frame(
            E_VERSION, f"contract version must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise TargetedStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    envelopes = request.get("inspect_results")
    if not isinstance(envelopes, list) or not envelopes:
        raise TargetedStageFault(make_error_frame(
            E_SCHEMA, "inspect_results must be a non-empty list"))
    for i, env in enumerate(envelopes):
        if not isinstance(env, dict) or env.get("stage") != "inspect":
            raise TargetedStageFault(make_error_frame(
                E_SCHEMA, f"inspect_results[{i}] is not a ⑤ inspect result"))
        result = env.get("result")
        if result is None or not isinstance(result.get("findings"), list):
            raise TargetedStageFault(make_error_frame(
                E_SCHEMA,
                f"inspect_results[{i}].result.findings must be a list"))
    computed = compute_idempotency_key(envelopes)
    if key != computed:
        raise TargetedStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical inspect_results bytes"))
    return envelopes


# --------------------------------------------------------------------------- #
# Request generation (deterministic; one request per finding)
# --------------------------------------------------------------------------- #
def generate_requests(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for env in envelopes:
        for finding in env["result"].get("findings", []):
            focus = finding.get("finding_id", "unknown")
            source = finding.get("source")
            priority = finding.get("priority", "low")
            code = finding.get("code", "unknown_gap")
            requests.append({
                "query_id": f"tq:{focus}",
                "focus_finding": focus,
                "target_source_id": source,
                "expected_dimension": code,
                "priority": priority,
                "query_hint": f"resolve {code} for {source}",
                "constraints": {"max_results": 5,
                                "time_cutoff_utc": None},
            })
    requests.sort(key=lambda r: r["query_id"])
    return requests


def run_targeted(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one ⑥ targeted-research request-generation batch."""
    try:
        envelopes = validate_request(request)
        requests = generate_requests(envelopes)
        env = make_envelope_head(request)
        env["result"] = {"requests": requests,
                         "counts": {"requests": len(requests)}}
        env["error"] = None
        return env
    except TargetedStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])


def canonical_output_bytes(envelope: dict[str, Any]) -> bytes:
    """Deterministic serialization for golden differential comparison."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
