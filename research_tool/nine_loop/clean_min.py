"""② clean minimal — nine-stage pipeline contract implementation
(P3 slice 7a, task RT-RF-P3-DELTA-MIN-01).

Consumes ① collect Result envelopes (frozen golden chain) and applies the
frozen §3 ②-row rules:
  - license gate: non-http(s) scheme → dropped (scheme_denied)
  - normalization: echo canonical_locator from the core identity
  - delta: content_sha256 already seen → skip (zero new output on re-run)

Offline, deterministic, stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "clean"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")

ALLOWED_SCHEMES = frozenset({"http", "https"})


def make_error_frame(code, safe_message, retryable=False):
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class CleanStageFault(Exception):
    def __init__(self, frame):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_input_bytes(collect_envelopes):
    return json.dumps(collect_envelopes, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(collect_envelopes):
    return hashlib.sha256(canonical_input_bytes(collect_envelopes)).hexdigest()


def make_envelope_head(request):
    return {"v": CONTRACT_VERSION, "run_id": request.get("run_id"),
            "stage": STAGE, "request_id": request.get("request_id"),
            "idempotency_key": request.get("idempotency_key"),
            "budget_lease": request.get("budget_lease")}


def _fail(request, code, safe_message, retryable=False):
    env = make_envelope_head(request)
    env["result"] = None
    env["error"] = make_error_frame(code, safe_message, retryable)
    return env


def request_from_collect(collect_env):
    import copy
    envelopes = [collect_env]
    return {"v": CONTRACT_VERSION, "run_id": collect_env.get("run_id"),
            "stage": STAGE,
            "request_id": "clean:" + str(collect_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(envelopes),
            "budget_lease": copy.deepcopy(collect_env.get("budget_lease")),
            "collect_results": envelopes}


def validate_request(request):
    if not isinstance(request, dict):
        raise CleanStageFault(make_error_frame(E_SCHEMA, "not an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise CleanStageFault(make_error_frame(
            E_VERSION, f"v must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise CleanStageFault(make_error_frame(E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise CleanStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise CleanStageFault(make_error_frame(E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise CleanStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise CleanStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing fields"))
    envelopes = request.get("collect_results")
    if not isinstance(envelopes, list) or not envelopes:
        raise CleanStageFault(make_error_frame(
            E_SCHEMA, "collect_results must be non-empty"))
    for i, env in enumerate(envelopes):
        if not isinstance(env, dict) or env.get("stage") != "collect":
            raise CleanStageFault(make_error_frame(
                E_SCHEMA, f"collect_results[{i}] not a ① collect result"))
        if not isinstance(env.get("result"), dict) \
                or not isinstance(env["result"].get("seeds"), list):
            raise CleanStageFault(make_error_frame(
                E_SCHEMA, f"collect_results[{i}] missing seeds"))
    computed = compute_idempotency_key(envelopes)
    if key != computed:
        raise CleanStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical collect_results"))
    return envelopes


def run_clean(request, seen_keys=None):
    """Run ② clean batch. seen_keys (set of (source_id, content_sha256))
    drives delta semantics: if provided, records with keys already present are
    skipped and zero new output is produced."""
    try:
        envelopes = validate_request(request)
    except CleanStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])
    if seen_keys is None:
        seen_keys = set()
    cleaned = []
    dropped = []
    new_facts = 0
    skipped = 0
    for env in envelopes:
        for seed in env["result"]["seeds"]:
            url = seed.get("url", "")
            scheme = url.split("://")[0] if "://" in url else ""
            if scheme.lower() not in ALLOWED_SCHEMES:
                dropped.append({"url": url, "reason": "scheme_denied",
                                "source_id": seed.get("source_id")})
                continue
            key = (seed.get("source_id"), seed.get("evidence_span", {}).get(
                "content_sha256"))
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)
            new_facts += 1
            cleaned.append({
                "url": url,
                "canonical_locator": seed.get("evidence_span", {}).get(
                    "locator", url),
                "source_id": seed.get("source_id"),
                "decision": seed.get("decision"),
                "content_sha256": seed.get("evidence_span", {}).get(
                    "content_sha256"),
                "clean_version": "clean.v1",
            })
    env = make_envelope_head(request)
    env["result"] = {"cleaned": cleaned, "dropped": dropped,
                     "counts": {"new_facts": new_facts, "skipped": skipped,
                                "dropped": len(dropped)}}
    env["error"] = None
    return env


def canonical_output_bytes(envelope):
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"