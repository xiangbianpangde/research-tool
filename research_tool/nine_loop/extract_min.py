"""③ extract minimal — nine-stage pipeline contract implementation
(P3 slice 7b, task RT-RF-P3-DELTA-MIN-01).

Consumes ② clean Result envelopes and applies the frozen §3 ③-row rules to
produce deterministic facts with mandatory evidence spans:

  - one fact per cleaned record; fact fields: fact_id, source_id,
    content_sha256, locator, extractor_version, evidence span
    {locator, start_offset, end_offset}
  - span = {locator: canonical_locator, start_offset: 0,
    end_offset: len(locator)} (deterministic; no external offset machinery)
  - delta: (source_id, content_sha256) already seen → skip (zero new output)

PURE RULE-BASED EXTRACTION: no LLM, no network — static tests assert no
model/network imports. Deterministic ordering; offline and stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE = "extract"
CONTRACT_VERSION = 1

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")

EXTRACTOR_VERSION = "extract.v1+rule"


def make_error_frame(code, safe_message, retryable=False):
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class ExtractStageFault(Exception):
    def __init__(self, frame):
        self.frame = frame
        super().__init__(frame["code"])


def canonical_input_bytes(clean_envelopes):
    return json.dumps(clean_envelopes, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(clean_envelopes):
    return hashlib.sha256(canonical_input_bytes(clean_envelopes)).hexdigest()


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


def request_from_clean(clean_env):
    import copy
    envelopes = [clean_env]
    return {"v": CONTRACT_VERSION, "run_id": clean_env.get("run_id"),
            "stage": STAGE,
            "request_id": "extract:" + str(clean_env.get("request_id")),
            "idempotency_key": compute_idempotency_key(envelopes),
            "budget_lease": copy.deepcopy(clean_env.get("budget_lease")),
            "clean_results": envelopes}


def validate_request(request):
    if not isinstance(request, dict):
        raise ExtractStageFault(make_error_frame(E_SCHEMA, "not an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise ExtractStageFault(make_error_frame(
            E_VERSION, f"v must be {CONTRACT_VERSION}"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise ExtractStageFault(make_error_frame(E_SCHEMA, "run_id missing"))
    if request.get("stage") != STAGE:
        raise ExtractStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise ExtractStageFault(make_error_frame(E_SCHEMA, "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise ExtractStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise ExtractStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing fields"))
    envelopes = request.get("clean_results")
    if not isinstance(envelopes, list) or not envelopes:
        raise ExtractStageFault(make_error_frame(
            E_SCHEMA, "clean_results must be non-empty"))
    for i, env in enumerate(envelopes):
        if not isinstance(env, dict) or env.get("stage") != "clean":
            raise ExtractStageFault(make_error_frame(
                E_SCHEMA, f"clean_results[{i}] not a ② clean result"))
        if not isinstance(env.get("result"), dict) \
                or not isinstance(env["result"].get("cleaned"), list):
            raise ExtractStageFault(make_error_frame(
                E_SCHEMA, f"clean_results[{i}] missing cleaned"))
    computed = compute_idempotency_key(envelopes)
    if key != computed:
        raise ExtractStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical clean_results"))
    return envelopes


def run_extract(request, seen_keys=None):
    """Run ③ extract batch. seen_keys drives delta semantics."""
    try:
        envelopes = validate_request(request)
    except ExtractStageFault as f:
        return _fail(request, f.frame["code"], f.frame["safe_message"],
                     f.frame["retryable"])
    if seen_keys is None:
        seen_keys = set()
    facts = []
    new_facts = 0
    skipped = 0
    for env in envelopes:
        for rec in env["result"]["cleaned"]:
            source_id = rec.get("source_id")
            ch = rec.get("content_sha256")
            key = (source_id, ch)
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)
            new_facts += 1
            locator = rec.get("canonical_locator", rec.get("url", ""))
            facts.append({
                "fact_id": f"fact:{source_id}",
                "source_id": source_id,
                "content_sha256": ch,
                "locator": locator,
                "extractor_version": EXTRACTOR_VERSION,
                "span": {"locator": locator, "start_offset": 0,
                         "end_offset": len(locator)},
            })
    facts.sort(key=lambda f: (f["source_id"], f["locator"]))
    env = make_envelope_head(request)
    env["result"] = {"facts": facts,
                     "counts": {"new_facts": new_facts, "skipped": skipped,
                                "facts": len(facts)}}
    env["error"] = None
    return env


def canonical_output_bytes(envelope):
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"