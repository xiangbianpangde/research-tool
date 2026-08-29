"""① collect stage — nine-stage pipeline contract implementation (P3 slice 1).

Implements the frozen contract from docs/plan/NINE-STAGE-CONTRACTS.md:
  §2 common envelope  — v / run_id / stage / request_id / idempotency_key /
                        budget_lease / typed error frame
  §3 ① row            — CollectRequest{seeds, crawl_policy, lease} ->
                        CollectResult{raw_snapshot_digest, seeds[], failures[]}

Identity determination is delegated to the accepted `rt-identity` core through
adapters.rt_identity_adapter.AdapterClient (child binary pinned by SHA-256).
This stage is OFFLINE: no DNS, no fetch, no LLM — seeds are resolved to
identities by the Rust core and wrapped with mandatory evidence spans.

Failure semantics (contract §4): any fault produces an envelope with a typed
error frame and NO partial result (partial-output forbidden).
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from typing import Any


from .rt_identity_adapter import (  # noqa: E402
    AdapterBinaryMismatch,
    AdapterClient,
    AdapterContractViolation,
    AdapterProtocolFatal,
    AdapterChildInternal,
    AdapterTimeout,
    idempotency_key,
)

STAGE = "collect"
CONTRACT_VERSION = 1
EXTRACTOR_VERSION = "collect.v1+rt-identity"

# Shared error codes (NINE-STAGE-CONTRACTS §4 registry)
E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"
E_LEASE_INVALID = "E_LEASE_INVALID"
E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
E_BUDGET_EXHAUSTED = "E_BUDGET_EXHAUSTED"
# Stage-namespaced codes
E_FATAL = "collect.E_FATAL"
E_CONTRACT = "collect.E_CONTRACT"

_LEASE_FIELDS = ("lease_id", "tokens_max", "cost_max", "wall_s_max",
                 "search_calls_max", "issued_at", "expires_at")


# --------------------------------------------------------------------------- #
# Typed error frame (contract §2)
# --------------------------------------------------------------------------- #
def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class CollectStageFault(Exception):
    """Internal typed fault; carries the contract error frame only."""

    def __init__(self, frame: dict[str, Any]):
        self.frame = frame
        super().__init__(frame["code"])


# --------------------------------------------------------------------------- #
# Envelope helpers
# --------------------------------------------------------------------------- #
def canonical_request_bytes(request: dict[str, Any]) -> bytes:
    """Deterministic bytes over the stage inputs (idempotency basis)."""
    return json.dumps(
        {"seeds": request.get("seeds", []),
         "crawl_policy": request.get("crawl_policy", {})},
        sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")


def compute_idempotency_key(request: dict[str, Any]) -> str:
    return idempotency_key(canonical_request_bytes(request))


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
# Request validation (contract §2/§3; explicit frames, no bare asserts)
# --------------------------------------------------------------------------- #
def validate_request(request: Any) -> None:
    if not isinstance(request, dict):
        raise CollectStageFault(make_error_frame(E_SCHEMA,
                                                 "request must be an object"))
    if request.get("v") != CONTRACT_VERSION:
        raise CollectStageFault(make_error_frame(
            E_VERSION,
            f"contract version must be {CONTRACT_VERSION}"))
    if request.get("run_id") is not None \
            and not isinstance(request.get("run_id"), str):
        raise CollectStageFault(make_error_frame(
            E_SCHEMA, "run_id must be str"))
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise CollectStageFault(make_error_frame(E_SCHEMA,
                                                 "run_id missing"))
    if request.get("stage") != STAGE:
        raise CollectStageFault(make_error_frame(
            E_SCHEMA, f"stage must be {STAGE!r}"))
    if not isinstance(request.get("request_id"), str) \
            or not request["request_id"]:
        raise CollectStageFault(make_error_frame(E_SCHEMA,
                                                 "request_id missing"))
    key = request.get("idempotency_key")
    if not isinstance(key, str) or len(key) != 64 \
            or any(ch not in "0123456789abcdef" for ch in key):
        raise CollectStageFault(make_error_frame(
            E_SCHEMA, "idempotency_key must be 64-hex"))
    lease = request.get("budget_lease")
    if not isinstance(lease, dict) \
            or any(f not in lease for f in _LEASE_FIELDS):
        raise CollectStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease missing required fields"))
    if not isinstance(lease.get("lease_id"), str) or not lease["lease_id"]:
        raise CollectStageFault(make_error_frame(
            E_LEASE_INVALID, "budget_lease.lease_id must be non-empty str"))
    for f in ("tokens_max", "cost_max", "wall_s_max", "search_calls_max"):
        v = lease.get(f)
        if type(v) not in (int, float) or v < 0:
            raise CollectStageFault(make_error_frame(
                E_LEASE_INVALID, f"budget_lease.{f} must be non-negative"))
    for f in ("issued_at", "expires_at"):
        if not isinstance(lease.get(f), str) or not lease[f]:
            raise CollectStageFault(make_error_frame(
                E_LEASE_INVALID, f"budget_lease.{f} must be UTC str"))
    seeds = request.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise CollectStageFault(make_error_frame(
            E_SCHEMA, "seeds must be a non-empty list"))
    for i, seed in enumerate(seeds):
        if not isinstance(seed, dict) \
                or not isinstance(seed.get("url"), str) or not seed["url"]:
            raise CollectStageFault(make_error_frame(
                E_SCHEMA, f"seeds[{i}].url must be non-empty str"))
    if not isinstance(request.get("crawl_policy", {}), dict):
        raise CollectStageFault(make_error_frame(
            E_SCHEMA, "crawl_policy must be an object"))
    computed = compute_idempotency_key(request)
    if key != computed:
        raise CollectStageFault(make_error_frame(
            E_IDEMPOTENCY_CONFLICT,
            "idempotency_key does not match canonical request bytes"))


# --------------------------------------------------------------------------- #
# Stage execution
# --------------------------------------------------------------------------- #
def _identity_request(request: dict[str, Any]) -> dict[str, Any]:
    hits = []
    for i, seed in enumerate(request["seeds"]):
        hits.append({"url": seed["url"],
                     "title": seed.get("title", ""),
                     "snippet": seed.get("snippet", ""),
                     "source_engine": "collect",
                     "audit_engine": "collect",
                     "rank": i,
                     "query_id": request["run_id"],
                     "content": seed.get("content")})
    return {"v": 1, "id": request["request_id"], "op": "identify",
            "hits": hits}


def _seed_record(identity: dict[str, Any],
                 request: dict[str, Any]) -> dict[str, Any]:
    return {"url": identity.get("input_url"),
            "source_id": identity.get("source_id"),
            "decision": identity.get("decision"),
            "evidence_span": {
                "source_id": identity.get("source_id"),
                "content_sha256": identity.get("content_sha256"),
                "locator": identity.get("canonical_locator"),
                "extractor_version": EXTRACTOR_VERSION,
                "round_id": request["request_id"],
            }}


def run_collect(request: dict[str, Any], client: AdapterClient) -> dict:
    """Execute one ① collect batch; returns a contract-compliant envelope.

    Success: envelope["result"] = {raw_snapshot_digest, seeds[], failures[],
    identity_stream_sha256}. Failure: envelope["error"] = typed frame and
    envelope["result"] = None (partial-output forbidden).
    """
    return run_collect_outcome(request, client).envelope


class CollectOutcome:
    """Success container: contract envelope + raw identity stream bytes
    (verbatim child stdout, for byte-level differential verification)."""

    __slots__ = ("envelope", "identity_stream")

    def __init__(self, envelope: dict[str, Any], identity_stream: bytes):
        self.envelope = envelope
        self.identity_stream = identity_stream


def run_collect_outcome(request: dict[str, Any],
                        client: AdapterClient) -> CollectOutcome:
    """Single-execution path: validate → drive child once via the adapter →
    build the contract envelope; keep the verbatim identity stream bytes."""
    try:
        validate_request(request)
        identity_req = _identity_request(request)
        payload = client.encode_requests([identity_req])
        wall = request["budget_lease"].get("wall_s_max")
        orig_deadline = client.deadline_s
        client.deadline_s = min(orig_deadline, float(wall)) \
            if wall else orig_deadline
        try:
            outcome = client.run_raw(payload)
        finally:
            client.deadline_s = orig_deadline
        record = outcome.records[0]
        if not record.get("ok"):
            err = record.get("error", {})
            raise CollectStageFault(make_error_frame(
                f"collect.{err.get('code', 'E_FATAL')}",
                str(err.get("safe_message", "identity core rejected batch")),
                False))
        seeds_out = [_seed_record(ident, request)
                     for ident in record.get("identities", [])]
        digest = hashlib.sha256(outcome.stdout_bytes).hexdigest()
        env = make_envelope_head(request)
        env["result"] = {
            "raw_snapshot_digest": digest,
            "seeds": seeds_out,
            "failures": [],
            "identity_stream_sha256": digest,
        }
        env["error"] = None
        return CollectOutcome(env, outcome.stdout_bytes)
    except CollectStageFault as f:
        return CollectOutcome(
            _fail(request, f.frame["code"], f.frame["safe_message"],
                  f.frame["retryable"]), b"")
    except AdapterTimeout:
        return CollectOutcome(
            _fail(request, E_BUDGET_EXHAUSTED,
                  "budget lease wall_s_max exhausted before child finished",
                  False), b"")
    except AdapterProtocolFatal as e:
        return CollectOutcome(
            _fail(request, E_FATAL,
                  f"protocol fatal exit {e.exit_code}", False), b"")
    except AdapterChildInternal as e:
        return CollectOutcome(
            _fail(request, E_FATAL,
                  f"child internal exit {e.exit_code}", False), b"")
    except AdapterContractViolation as e:
        return CollectOutcome(
            _fail(request, E_CONTRACT, e.reason, False), b"")
    except AdapterBinaryMismatch:
        return CollectOutcome(
            _fail(request, E_FATAL,
                  "child binary sha256 mismatch; refusing to start", False), b"")
