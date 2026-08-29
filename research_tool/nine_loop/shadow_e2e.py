"""Full-pipeline shadow E2E — P4 slice 2 (task RT-RF-P4-SHADOW-E2E-01).

Drives the ACCEPTED e2e chain (pipeline/e2e.py, five-stage composition:
①→④→⑤→⑧→⑨) under shadow mode on frozen input, comparing the legacy
passthrough view against the nine-stage view and producing a divergence
report per the design schema (INTEGRATION-FLAG-SHADOW-DESIGN.md §3).
Rollback mode (flags off) → legacy-only, byte-identical behavior.

Composition (design §2/§3):
  flags on + shadow sampling → ShadowRunner(nine_path=full chain)
  flags off                   → legacy-only (nothing written anywhere)

Offline: the nine-stage chain runs against the pinned rt-identity child via
the accepted adapter client (no network/LLM/DRB). stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from typing import Any, Callable


from . import e2e  # noqa: E402
from . import flags  # noqa: E402
from . import shadow  # noqa: E402

STAGE = "shadow_e2e"
CONTRACT_VERSION = 1

E_SCHEMA = "E_SCHEMA"
E_LEASE_ZERO = "shadow_e2e.E_LEASE_ZERO"
E_SHADOW = "shadow.E_SHADOW"

# Shadow runs OFFLINE (no search calls); the search_calls cap is not part of
# the zero-capacity skip decision. Zero wall/tokens/cost → typed skip.
_CAP_FIELDS = ("wall_s_max", "tokens_max", "cost_max")


class ShadowE2EFault(Exception):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def lease_zero_capacity(lease: dict[str, Any] | None) -> bool:
    """True when any shadow cap (wall/tokens/cost) is zero or negative."""
    if not isinstance(lease, dict):
        return False
    for f in _CAP_FIELDS:
        v = lease.get(f)
        if type(v) in (int, float) and not isinstance(v, bool) and v <= 0:
            return True
    return False


def legacy_view_from_request(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic in-memory passthrough view of the frozen request seeds.

    case_id = seed url (fallback seed-<i>); data = title/snippet + a
    content digest when the seed carries inline content. NEVER persisted —
    the legacy path has no write surface (isolation proof).
    """
    seeds = request.get("seeds")
    if not isinstance(seeds, list):
        raise ShadowE2EFault(E_SCHEMA, "request seeds must be a list")
    out: list[dict[str, Any]] = []
    for i, s in enumerate(seeds):
        if not isinstance(s, dict):
            raise ShadowE2EFault(E_SCHEMA, f"seeds[{i}] must be an object")
        case_id = s.get("url") or f"seed-{i}"
        content = s.get("content")
        out.append({"case_id": case_id,
                    "data": {"title": s.get("title", ""),
                             "snippet": s.get("snippet", ""),
                             "content_sha256": _sha(content) if content
                             else None}})
    out.sort(key=lambda r: str(r["case_id"]))
    return out


def nine_view_from_report(env: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic view of the e2e final report envelope (claims only)."""
    report = env.get("result", {}).get("report") or {}
    claims = report.get("claims", [])
    out = [{"case_id": c.get("claim_id"),
            "data": {"text": c.get("text", ""),
                     "citations": len(c.get("citations", []))}}
           for c in claims if isinstance(c, dict)]
    out.sort(key=lambda r: str(r["case_id"]))
    return out


def make_nine_path(request: dict[str, Any], client: Any,
                   chain_root: pathlib.Path) -> Callable[[str], dict]:
    """Returns a nine-path callable: (run_id) -> envelope with records view.

    Each invocation gets its own chain work dir under chain_root/<run_id> so
    repeated runs never collide (E2E idempotency semantics preserved)."""
    chain_root = pathlib.Path(chain_root)

    def nine_path(run_id: str) -> dict[str, Any]:
        wd = chain_root / str(run_id)
        chain = e2e.E2EChain(client, wd)
        req = dict(request)
        req["run_id"] = str(run_id)
        env = chain.run(req)
        return {"v": CONTRACT_VERSION, "run_id": str(run_id),
                "stage": "nine",
                "result": {"records": nine_view_from_report(env)},
                "error": None}

    return nine_path


class ShadowE2E:
    """Full-pipeline shadow composition (design §2/§3/§5 semantics)."""

    def __init__(self, cfg: dict[str, Any] | None = None,
                 client: Any = None,
                 work_root: str | pathlib.Path | None = None,
                 chain_root: str | pathlib.Path | None = None):
        self.cfg = flags.load(cfg)
        self.client = client
        self.work_root = pathlib.Path(work_root) if work_root else None
        self.chain_root = pathlib.Path(chain_root) if chain_root else None

    # -- public entry ---------------------------------------------------- #
    def run(self, request: dict[str, Any], run_id: str) -> dict[str, Any]:
        """One full-pipeline shadow run (or legacy-only when flags off).

        Returns a typed envelope; never raises for nine-path failures
        (recorded as nine_error) and never writes legacy outputs.
        """
        if not isinstance(request, dict):
            raise ShadowE2EFault(E_SCHEMA, "request must be an object")
        legacy_records = legacy_view_from_request(request)
        legacy_digest = shadow.digest_of(legacy_records)

        if lease_zero_capacity(request.get("budget_lease")):
            return {"v": CONTRACT_VERSION, "run_id": str(run_id),
                    "stage": STAGE, "sampled": False, "legacy_only": False,
                    "skipped": "lease_zero_capacity",
                    "legacy_digest": legacy_digest,
                    "nine_digest": "", "divergence_count": 0,
                    "divergences": [], "nine_error": None}

        if not flags.shadow_enabled(self.cfg):
            # rollback mode: pure legacy, nothing written anywhere
            return {"v": CONTRACT_VERSION, "run_id": str(run_id),
                    "stage": STAGE, "sampled": False, "legacy_only": True,
                    "skipped": None, "legacy_digest": legacy_digest,
                    "nine_digest": "", "divergence_count": 0,
                    "divergences": [], "nine_error": None}

        if self.client is None or self.chain_root is None:
            raise ShadowE2EFault(E_SCHEMA,
                                 "shadow run requires client and chain_root")
        runner = shadow.ShadowRunner(
            cfg=self.cfg,
            nine_path=make_nine_path(request, self.client, self.chain_root),
            work_root=self.work_root)
        return runner.run({"records": legacy_records}, run_id)
