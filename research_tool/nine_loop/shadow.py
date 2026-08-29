"""Shadow runner — P4 slice 1 (task RT-RF-P4-FLAG-SHADOW-IMPL-01).

Implements §3/§4/§5 of docs/plan/INTEGRATION-FLAG-SHADOW-DESIGN.md:
same-input dual-run (legacy passthrough + nine-stage path), isolated
shadow/<run_id>/ output root, zero user-visible effect, never writes back to
legacy outputs, divergence capture per the design schema, deterministic
sampling, atomic artifact writes (no partial shadow artifacts).

Isolation proofs (F1 negative path): with all flags OFF (default), the
runner physically cannot open any legacy output path — its only write
surface is the caller-provided shadow work root, and legacy passthrough
records are computed in memory (never persisted outside shadow root).

stdlib-only; no network/LLM/DRB; no /tmp.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any, Callable

from . import flags

STAGE = "shadow"
CONTRACT_VERSION = 1

E_SCHEMA = "E_SCHEMA"
E_SHADOW = "shadow.E_SHADOW"

DIFF_TYPES = ("missing", "extra", "value", "order")

# Divergence schema (design §3): {run_id, stage, case_id, legacy_digest,
# nine_digest, diff_type, field_path} — digests/paths only, never URL payloads
# or secrets.
_DIVERGENCE_FIELDS = ("run_id", "stage", "case_id", "legacy_digest",
                      "nine_digest", "diff_type", "field_path")


def make_error_frame(code: str, safe_message: str,
                     retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": STAGE, "safe_message": safe_message,
            "retryable": retryable}


class ShadowFault(Exception):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def digest_of(obj: Any) -> str:
    return hashlib.sha256(flags.canonical_bytes(obj)).hexdigest()


def sha256_file(p) -> str:
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def write_atomic(target: pathlib.Path, data: bytes) -> None:
    """Same-dir tmp + fsync + os.replace; crash leaves no partial artifact."""
    tmp = target.with_name(target.name + ".tmp-" + str(os.getpid()))
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Divergence comparator (design §3 schema)
# --------------------------------------------------------------------------- #
def _leaf_diffs(legacy: dict[str, Any], nine: dict[str, Any],
                prefix: str = "") -> list[tuple[str, str, str, str]]:
    """Recursive value diff → (field_path, legacy_digest, nine_digest, type)."""
    out: list[tuple[str, str, str, str]] = []
    keys = sorted(set(legacy) | set(nine))
    for k in keys:
        path = f"{prefix}.{k}" if prefix else k
        if k not in legacy:
            out.append((path, "", digest_of(nine[k]), "extra"))
        elif k not in nine:
            out.append((path, digest_of(legacy[k]), "", "missing"))
        elif isinstance(legacy[k], dict) and isinstance(nine[k], dict):
            out.extend(_leaf_diffs(legacy[k], nine[k], path))
        elif isinstance(legacy[k], list) and isinstance(nine[k], list) \
                and legacy[k] != nine[k]:
            # order diff: same multiset in different order; else value diff
            if sorted(map(digest_of, legacy[k])) == sorted(
                    map(digest_of, nine[k])):
                out.append((path, digest_of(legacy[k]),
                            digest_of(nine[k]), "order"))
            else:
                out.append((path, digest_of(legacy[k]),
                            digest_of(nine[k]), "value"))
        elif legacy[k] != nine[k]:
            out.append((path, digest_of(legacy[k]),
                        digest_of(nine[k]), "value"))
    return out


def compare(legacy_records: list[dict[str, Any]],
            nine_records: list[dict[str, Any]],
            run_id: str, stage: str) -> list[dict[str, Any]]:
    """Compare two record lists per design §3 divergence schema.

    missing: case in legacy only; extra: case in nine only; value/order:
    shared case with differing content/order. Sorted deterministically by
    (case_id, field_path). No URL payloads or secrets — digests only.
    """
    divergences: list[dict[str, Any]] = []
    legacy_by_id = {r["case_id"]: r for r in legacy_records}
    nine_by_id = {r["case_id"]: r for r in nine_records}

    for cid in sorted(set(legacy_by_id) - set(nine_by_id)):
        divergences.append({
            "run_id": run_id, "stage": stage, "case_id": cid,
            "legacy_digest": digest_of(legacy_by_id[cid]),
            "nine_digest": "", "diff_type": "missing",
            "field_path": "$record"})

    for cid in sorted(set(nine_by_id) - set(legacy_by_id)):
        divergences.append({
            "run_id": run_id, "stage": stage, "case_id": cid,
            "legacy_digest": "", "nine_digest": digest_of(nine_by_id[cid]),
            "diff_type": "extra", "field_path": "$record"})

    for cid in sorted(set(legacy_by_id) & set(nine_by_id)):
        lg, ng = legacy_by_id[cid], nine_by_id[cid]
        for path, ld, nd, dtype in _leaf_diffs(lg, ng):
            divergences.append({
                "run_id": run_id, "stage": stage, "case_id": cid,
                "legacy_digest": ld, "nine_digest": nd,
                "diff_type": dtype, "field_path": path})

    divergences.sort(key=lambda d: (d["case_id"], d["field_path"],
                                    d["diff_type"]))
    return divergences


# --------------------------------------------------------------------------- #
# Shadow runner
# --------------------------------------------------------------------------- #
class ShadowRunner:
    """Dual-run harness: legacy passthrough + injected nine-stage path.

    The nine-stage path is injected as a callable (run_id) -> envelope so the
    harness stays testable offline; production wiring passes the e2e chain
    (e2e.py E2EChain.run). Legacy passthrough is computed in memory only —
    the sole persisted output is shadow/<run_id>/ under the shadow work root.
    """

    def __init__(self, cfg: dict[str, Any] | None = None,
                 nine_path: Callable[[str], dict[str, Any]] | None = None,
                 work_root: str | pathlib.Path | None = None):
        self.cfg = flags.load(cfg)
        self.nine_path = nine_path
        self.work_root = pathlib.Path(work_root) if work_root else None

    # -- sampling --------------------------------------------------------- #
    def sampled(self, run_id: str) -> bool:
        return flags.sample_hit(self.cfg, run_id)

    # -- legacy passthrough ----------------------------------------------- #
    def legacy_passthrough(self, input_snapshot: dict[str, Any]) -> list[dict]:
        """Deterministic no-op view of the input records (in-memory only)."""
        records = input_snapshot.get("records", [])
        out = []
        for rec in records:
            if not isinstance(rec, dict) or "case_id" not in rec:
                raise ShadowFault(
                    E_SCHEMA,
                    "input snapshot records must carry case_id")
            out.append({"case_id": rec["case_id"], "data": rec.get("data")})
        out.sort(key=lambda r: str(r["case_id"]))
        return out

    # -- main entry ------------------------------------------------------- #
    def run(self, input_snapshot: dict[str, Any], run_id: str) -> dict[str, Any]:
        """Execute one shadow dual-run; returns the shadow envelope.

        Legacy passthrough is always computed (in memory). The nine-stage
        path runs only when sampled. Output root is written ATOMICALLY only
        after full in-memory success — a crash in the nine path leaves zero
        partial shadow artifacts and never touches legacy outputs.
        """
        if not isinstance(input_snapshot, dict) \
                or not isinstance(input_snapshot.get("records"), list):
            raise ShadowFault(E_SCHEMA,
                              "input snapshot must carry records[]")
        legacy_records = self.legacy_passthrough(input_snapshot)

        nine_records: list[dict] = []
        divergences: list[dict] = []
        nine_error: str | None = None
        if self.sampled(run_id):
            if self.nine_path is None:
                raise ShadowFault(
                    E_SCHEMA,
                    "sampled run requires a nine-stage path")
            try:
                env = self.nine_path(run_id)
                if not isinstance(env, dict) or env.get("error") is not None:
                    raise ShadowFault(
                        E_SHADOW,
                        "nine-stage path failed; legacy unaffected")
                nine_records = env.get("result", {}).get("records", [])
                divergences = compare(legacy_records, nine_records,
                                      run_id, "nine")
            except ShadowFault:
                raise
            except Exception as e:
                # shadow crash must not affect legacy: report, don't raise
                nine_error = (f"shadow nine-stage path crashed: "
                              f"{type(e).__name__}")
        elif self.nine_path is not None:
            # negative-path guard: shadow disabled ⇒ nine path never invoked
            nine_error = None

        envelope = {
            "v": CONTRACT_VERSION,
            "run_id": run_id,
            "stage": STAGE,
            "sampled": self.sampled(run_id),
            "legacy_digest": digest_of(legacy_records),
            "nine_digest": digest_of(nine_records) if nine_records else "",
            "divergence_count": len(divergences),
            "divergences": divergences,
            "nine_error": nine_error,
        }
        # persist ONLY when sampled: an unsampled run (shadow switch off or
        # rate 0) writes nothing at all — the F1 negative path is physical
        if envelope["sampled"]:
            self._persist(run_id, envelope)
        return envelope

    def _persist(self, run_id: str, envelope: dict[str, Any]) -> None:
        if self.work_root is None:
            return
        out_dir = self.work_root / "shadow" / str(run_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(
            out_dir / "manifest.json",
            json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8") + b"\n")
