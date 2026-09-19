"""Atomic ChainState & Checkpointing Engine (Protocol v1).

Implements durable atomic stage envelope commits, SHA-256 fingerprint verification,
canonical stage alias normalization, and crash-resilient state transitions.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import uuid
from typing import Any

STATE_VERSION = 1

E_IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
E_STATE = "E_STATE"
E_CAS_CONFLICT = "E_CAS_CONFLICT"

STAGE_CANONICAL: dict[str, str] = {
    "knowledge": "network",
    "qgate": "gate",
    "organize": "network",
}

STAGE_ALIASES: dict[str, str] = {
    "knowledge": "network",
    "network": "knowledge",
    "qgate": "gate",
    "gate": "qgate",
    "organize": "knowledge",
}


def canonical_stage_name(stage: str) -> str:
    s = stage.strip().lower()
    return STAGE_CANONICAL.get(s, s)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path | str) -> str:
    return sha256_bytes(pathlib.Path(path).read_bytes())


def write_atomic(target: pathlib.Path, data: bytes) -> None:
    """Atomic write: writes to same-directory temporary file, fsyncs, and replaces."""
    target.parent.mkdir(parents=True, exist_ok=True)
    unique_suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp = target.with_name(f"{target.name}.tmp-{unique_suffix}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


class ChainStateFault(Exception):
    """Typed fault carrying an error code and contract-safe message."""

    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(f"{code}: {safe_message}")


E2EFault = ChainStateFault


class ChainState:
    """Durable state machine and artifact repository under work_dir."""

    def __init__(self, work_dir: pathlib.Path | str):
        self.work_dir = pathlib.Path(work_dir)
        self.artifacts = self.work_dir / "artifacts"
        self.state_path = self.work_dir / "state.json"

    def cleanup_tmps(self) -> int:
        """Removes any stale .tmp-* files from aborted runs."""
        cleaned = 0
        for root in (self.work_dir, self.artifacts):
            if root.exists():
                for p in root.glob("*.tmp-*"):
                    try:
                        p.unlink()
                        cleaned += 1
                    except OSError:
                        pass
        return cleaned

    def load(self, input_key: str, resume: bool = True) -> dict[str, Any]:
        """Loads state manifest, verifying input idempotency key and artifact hashes on resume."""
        self.cleanup_tmps()
        if not self.state_path.exists():
            return self.reset(input_key)

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("version") != STATE_VERSION:
            raise ChainStateFault(E_STATE, f"unsupported state version {state.get('version')}")

        if state.get("input_idempotency_key") != input_key:
            raise ChainStateFault(
                E_IDEMPOTENCY_CONFLICT,
                f"work dir state belongs to a different input idempotency key: "
                f"recorded={state.get('input_idempotency_key')}, expected={input_key}",
            )

        if resume:
            self.verify_state(state)
        return state

    def reset(self, input_key: str) -> dict[str, Any]:
        """Initializes a fresh state manifest for the given input key."""
        state = {
            "version": STATE_VERSION,
            "input_idempotency_key": input_key,
            "stages": {},
            "done": [],
            "loop": {
                "current_round": 0,
                "latest_network_digest": None,
                "history": [],
            },
            "error": None,
        }
        self._save_state(state)
        return state

    def verify_state(self, state: dict[str, Any]) -> None:
        """Validates that all completed stages have uncorrupted artifacts matching their SHA-256."""
        stages_done = state.get("done", [])
        for stage in stages_done:
            target = self.artifacts / f"{stage}.json"
            alias = STAGE_ALIASES.get(stage)
            if not target.exists():
                if alias and (self.artifacts / f"{alias}.json").exists():
                    target = self.artifacts / f"{alias}.json"
                else:
                    raise ChainStateFault(E_STATE, f"missing artifact for completed stage {stage}")

            actual_sha = sha256_bytes(target.read_bytes())
            expected_sha = state.get("stages", {}).get(stage)
            if expected_sha is None and alias:
                expected_sha = state.get("stages", {}).get(alias)

            if actual_sha != expected_sha:
                raise ChainStateFault(
                    E_STATE,
                    f"artifact digest mismatch for {stage}: computed={actual_sha}, expected={expected_sha}",
                )

    def is_stage_complete(self, state: dict[str, Any], stage: str) -> bool:
        """Checks whether the stage is durably committed and present on disk."""
        alias = STAGE_ALIASES.get(stage)
        done_list = state.get("done", [])
        is_done = stage in done_list or (alias and alias in done_list)
        if not is_done:
            return False

        target = self.artifacts / f"{stage}.json"
        if target.exists():
            return True
        if alias and (self.artifacts / f"{alias}.json").exists():
            return True
        return False

    def is_stage_done(self, state: dict[str, Any], stage: str) -> bool:
        return self.is_stage_complete(state, stage)

    def commit_stage(
        self, state: dict[str, Any], stage: str, envelope: dict[str, Any]
    ) -> dict[str, Any]:
        """Atomically persists stage artifact envelope and updates state.json."""
        data = json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        target = self.artifacts / f"{stage}.json"
        write_atomic(target, data)
        digest = sha256_bytes(data)

        state["stages"][stage] = digest
        if stage not in state["done"]:
            state["done"].append(stage)

        # Mirror canonical/alias pairs so either representation is queryable
        alias = STAGE_ALIASES.get(stage)
        if alias:
            alias_target = self.artifacts / f"{alias}.json"
            if not alias_target.exists() or alias_target.read_bytes() != data:
                write_atomic(alias_target, data)
            state["stages"][alias] = digest

        self._save_state(state)
        return state

    def commit_loop_merge(
        self,
        state: dict[str, Any],
        network_env: dict[str, Any],
        merge_env: dict[str, Any],
        round_idx: int,
    ) -> dict[str, Any]:
        """Atomically commits updated knowledge network and merge audit envelope in a single transaction."""
        net_data = json.dumps(
            network_env, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        merge_data = json.dumps(
            merge_env, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        net_target = self.artifacts / "network.json"
        knowledge_target = self.artifacts / "knowledge.json"
        merge_target = self.artifacts / "merge.json"

        write_atomic(net_target, net_data)
        write_atomic(knowledge_target, net_data)
        write_atomic(merge_target, merge_data)

        net_sha = sha256_bytes(net_data)
        merge_sha = sha256_bytes(merge_data)

        state["stages"]["network"] = net_sha
        state["stages"]["knowledge"] = net_sha
        state["stages"]["merge"] = merge_sha

        for s in ("network", "knowledge", "merge"):
            if s not in state["done"]:
                state["done"].append(s)

        latest_digest = merge_env.get("result", {}).get("latest_digest")
        state["loop"]["current_round"] = round_idx + 1
        state["loop"]["latest_network_digest"] = latest_digest
        state["loop"]["history"].append(
            {
                "round": round_idx,
                "latest_digest": latest_digest,
                "counts": merge_env.get("result", {}).get("counts", {}),
            }
        )
        self._save_state(state)
        return state

    def read_stage(self, state: dict[str, Any], stage: str) -> dict[str, Any]:
        """Reads and verifies a persisted stage envelope."""
        target = self.artifacts / f"{stage}.json"
        alias = STAGE_ALIASES.get(stage)
        if not target.exists():
            if alias and (self.artifacts / f"{alias}.json").exists():
                target = self.artifacts / f"{alias}.json"
            else:
                raise ChainStateFault(E_STATE, f"missing artifact for stage {stage}")

        data = target.read_bytes()
        actual_sha = sha256_bytes(data)
        expected_sha = state.get("stages", {}).get(stage)
        if expected_sha is None and alias:
            expected_sha = state.get("stages", {}).get(alias)

        if expected_sha is not None and actual_sha != expected_sha:
            raise ChainStateFault(
                E_STATE,
                f"artifact digest mismatch for {stage}: computed={actual_sha}, expected={expected_sha}",
            )
        return json.loads(data.decode("utf-8"))

    def _save_state(self, state: dict[str, Any]) -> None:
        raw = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        write_atomic(self.state_path, raw)
