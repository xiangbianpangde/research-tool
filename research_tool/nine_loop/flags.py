"""Feature-flag evaluation — P4 slice 1 (task RT-RF-P4-FLAG-SHADOW-IMPL-01).

Implements §2 of docs/plan/INTEGRATION-FLAG-SHADOW-DESIGN.md (B10 design,
review cb8a7fcf…): flag architecture, precedence, default-off, deterministic
sampling. Design is the contract; this module is stdlib-only and reads NO
environment at import time — configuration is injected explicitly.

Flag set (per design §2 table):
  nine_loop.enabled                master kill-switch (false → ignore all)
  nine_loop.stages.{collect,clean,extract,network,inspect,delta,merge,report}
                                   per-stage switches (contract §7 alignment)
  nine_loop.gate.enabled           ⑧ quality-gate wiring
  nine_loop.deepen_as_strategy     deepen demoted to ①/⑥ optional strategy
  rt_identity.adapter.enabled      identity via rt-identity adapter
  nine_loop.shadow.enabled         shadow dual-run master switch
  nine_loop.shadow.sample_rate     sample rate (0 = never sample)

Precedence: kill-switch > stage flags > shadow flags. Evaluation happens at a
single assembly point; the runner never re-reads flags mid-run.

Deterministic sampling: design says `hash(run_id) % N < rate*N` — Python's
builtin hash() for str is randomized per-process (PYTHONHASHSEED), which would
break the required reproducibility, so the implementation uses
sha256(run_id) as the deterministic hash source (same semantics, auditably
reproducible across processes).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STAGE_FLAGS = ("collect", "clean", "extract", "network", "inspect", "delta",
               "merge", "report")

FLAG_DEFAULTS: dict[str, Any] = {
    "nine_loop.enabled": False,
    "nine_loop.gate.enabled": False,
    "nine_loop.deepen_as_strategy": False,
    "rt_identity.adapter.enabled": False,
    "nine_loop.shadow.enabled": False,
    "nine_loop.shadow.sample_rate": 0,
}
for _s in STAGE_FLAGS:
    FLAG_DEFAULTS[f"nine_loop.stages.{_s}"] = False

_BOOL_FLAGS = tuple(
    k for k, v in FLAG_DEFAULTS.items() if isinstance(v, bool))
_SAMPLE_RATE_FLAG = "nine_loop.shadow.sample_rate"

E_VERSION = "E_VERSION"
E_SCHEMA = "E_SCHEMA"


class FlagsFault(Exception):
    """Typed fault carrying a contract-style error frame (no payloads)."""

    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def error_frame(code: str, safe_message: str,
                retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "stage": "flags", "safe_message": safe_message,
            "retryable": retryable}


def validate_config(config: dict[str, Any]) -> None:
    """Structural validation of an injected config; explicit typed errors."""
    if not isinstance(config, dict):
        raise FlagsFault(E_SCHEMA, "config must be an object")
    for key, value in config.items():
        if key not in FLAG_DEFAULTS:
            raise FlagsFault(E_SCHEMA, f"unknown flag {key!r}")
        if key in _BOOL_FLAGS:
            if not isinstance(value, bool):
                raise FlagsFault(
                    E_SCHEMA, f"flag {key!r} must be a bool")
        elif key == _SAMPLE_RATE_FLAG:
            if type(value) not in (int, float) \
                    or isinstance(value, bool) \
                    or not (0.0 <= float(value) <= 1.0):
                raise FlagsFault(
                    E_SCHEMA,
                    f"{_SAMPLE_RATE_FLAG} must be a number in [0, 1]")


def load(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge an injected config over FLAG_DEFAULTS (no env reads)."""
    cfg = dict(FLAG_DEFAULTS)
    if config:
        validate_config(config)
        cfg.update(config)
    return cfg


def snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    """Deterministic snapshot for run manifests (sorted keys)."""
    return json.loads(json.dumps(cfg, sort_keys=True))


def kill_switch(cfg: dict[str, Any]) -> bool:
    return bool(cfg["nine_loop.enabled"])


def stage_enabled(cfg: dict[str, Any], stage: str) -> bool:
    if stage not in STAGE_FLAGS:
        raise FlagsFault(E_SCHEMA, f"unknown stage {stage!r}")
    if not kill_switch(cfg):
        return False
    return bool(cfg[f"nine_loop.stages.{stage}"])


def gate_enabled(cfg: dict[str, Any]) -> bool:
    return kill_switch(cfg) and bool(cfg["nine_loop.gate.enabled"])


def deepen_enabled(cfg: dict[str, Any]) -> bool:
    return kill_switch(cfg) and bool(cfg["nine_loop.deepen_as_strategy"])


def adapter_enabled(cfg: dict[str, Any]) -> bool:
    return kill_switch(cfg) and bool(cfg["rt_identity.adapter.enabled"])


def shadow_enabled(cfg: dict[str, Any]) -> bool:
    return kill_switch(cfg) and bool(cfg["nine_loop.shadow.enabled"])


def sample_rate(cfg: dict[str, Any]) -> float:
    return float(cfg[_SAMPLE_RATE_FLAG])


def sample_hit(cfg: dict[str, Any], run_id: str) -> bool:
    """Deterministic sampling: sha256(run_id) % 2**32 < rate * 2**32.

    Shadow master switch (and hence the kill-switch) must be on; rate=0 →
    never hit; rate=1 → always hit; identical run_id ⇒ identical verdict
    (reproducible and auditable across processes).
    """
    if not shadow_enabled(cfg):
        return False
    rate = sample_rate(cfg)
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(str(run_id).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return (value % (2 ** 32)) < (rate * (2 ** 32))


def resolve(cfg: dict[str, Any]) -> dict[str, Any]:
    """Single assembly-point evaluation (design §2: evaluate once, never
    re-read mid-run). Returns the full wiring decision snapshot."""
    if not kill_switch(cfg):
        # kill-switch off: everything routes to legacy, ignore sub-flags
        return {
            "legacy": True,
            "nine_loop_enabled": False,
            "stages": {s: False for s in STAGE_FLAGS},
            "gate_enabled": False,
            "deepen_as_strategy": False,
            "adapter_enabled": False,
            "shadow_enabled": False,
            "sample_rate": 0.0,
        }
    return {
        "legacy": False,
        "nine_loop_enabled": True,
        "stages": {s: stage_enabled(cfg, s) for s in STAGE_FLAGS},
        "gate_enabled": gate_enabled(cfg),
        "deepen_as_strategy": deepen_enabled(cfg),
        "adapter_enabled": adapter_enabled(cfg),
        "shadow_enabled": shadow_enabled(cfg),
        "sample_rate": sample_rate(cfg),
    }


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def verify_flags(package_root: "pathlib.Path | None" = None) -> dict[str, Any]:
    """Single-source assertion: nine_loop/flags.py is the ONLY runtime flags
    source. Scans the product package for competing FLAG_DEFAULTS definitions
    (duplicate modules). Loud failure on multi-source detection.

    Returns {"sources": ["research_tool.nine_loop.flags"], "count": 1}.
    Raises FlagsFault(E_VERSION) if >1 source found.
    """
    import importlib
    import pathlib as _pl

    root = _pl.Path(__file__).resolve().parents[1] if package_root is None         else _pl.Path(package_root)
    sources = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root.parent)
        # tests are not runtime sources; skip them (their FLAG_DEFAULTS mentions
        # are test fixtures, not loadable flag definitions)
        if (p.parts[-2] if len(p.parts) >= 2 else "") == "tests":
            continue
        if "__pycache__" in p.parts or p.name == "__init__.py":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "FLAG_DEFAULTS: dict" in text or "FLAG_DEFAULTS =" in text:
            mod = ".".join(rel.with_suffix("").parts)
            sources.append(mod)
    if len(sources) != 1 or sources[0] != "research_tool.nine_loop.flags":
        raise FlagsFault(E_VERSION,
                         f"flags multi-source detected: {sources}; "
                         "single source must be research_tool.nine_loop.flags")
    return {"sources": sources, "count": len(sources)}
