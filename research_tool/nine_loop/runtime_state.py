"""Durable, CAS-protected state for the nine-stage runtime loop."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import time
from errno import EACCES, EAGAIN, EDEADLK
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt


def _windows_lock(handle, *, nonblocking: bool) -> None:
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            busy = exc.errno in {EACCES, EAGAIN, EDEADLK}
            if not busy or nonblocking:
                raise
            time.sleep(0.01)


class RuntimeStateError(RuntimeError):
    """Raised when runtime state or its recorded artifacts are invalid."""


class RuntimeState:
    """Persist and validate the state of one nine-stage run."""

    STATE_VERSION = 1
    _STATE_FILENAME = "runtime-state.json"
    _STAGE_PATTERN = re.compile(
        r"^(?:collect|clean|extract|knowledge|inspect|qgate|targeted|merge|report)"
        r"(?::[0-9]+)?$"
    )

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.state_path = self.root / self._STATE_FILENAME

    def load_or_init(self, input_fingerprint: str) -> dict[str, Any]:
        """Load and validate state, or atomically initialize a new run."""
        self._validate_fingerprint(input_fingerprint)
        with self._lock("runtime-state.lock"):
            if self.state_path.exists():
                state = self._read_state()
                self._validate_state(state)
                if state["input_fingerprint"] == input_fingerprint:
                    return state
            state = self._new_state(input_fingerprint)
            self._write_state(state)
            return state

    def read_current(self) -> dict[str, Any]:
        """Read and validate the current state without creating a lock file.

        State writes use atomic replacement, so consumers can validate a
        complete snapshot directly.  Read-only commands must not create files
        in a topic directory merely by inspecting it.
        """
        state = self._read_state()
        self._validate_state(state)
        return state

    def start_new(self, input_fingerprint: str) -> dict[str, Any]:
        """Explicitly start a new run without deleting any prior artifacts."""
        self._validate_fingerprint(input_fingerprint)
        with self._lock("runtime-state.lock"):
            state = self._new_state(input_fingerprint)
            self._write_state(state)
            return state

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        """Exclusively guard a whole run; fail immediately if already held.

        Callers hold this context around initialization, artifact creation and
        commits. State transactions acquire a separate lock inside it.
        """
        with self._lock("runtime-state.run.lock", nonblocking=True):
            yield

    def commit_stage(
        self,
        stage: str,
        artifact_path: Path,
        artifact_digest: str,
        expected_latest_digest: str | None,
    ) -> dict[str, Any]:
        """Commit one immutable stage artifact using a latest-digest CAS."""
        self._validate_stage(stage)
        artifact = Path(artifact_path)
        if not isinstance(artifact_digest, str) or not artifact_digest:
            raise RuntimeStateError("artifact_digest must be a non-empty string")

        with self._lock("runtime-state.lock"):
            state = self._read_state()
            self._validate_state(state)
            if state["latest_digest"] != expected_latest_digest:
                raise RuntimeStateError("CAS conflict: latest digest does not match")
            self._validate_artifact_path(artifact)
            actual_digest = self._sha256_file(artifact)
            if actual_digest != artifact_digest:
                raise RuntimeStateError(
                    f"artifact digest mismatch for {artifact}: expected {artifact_digest}, "
                    f"got {actual_digest}"
                )
            if stage in state["completed_stages"]:
                raise RuntimeStateError(f"stage already committed: {stage}")

            completed = dict(state["completed_stages"])
            completed[stage] = {
                "artifact_path": artifact.relative_to(self.root).as_posix(),
                "artifact_digest": artifact_digest,
            }
            committed_round = self._round_for_stage(stage)
            updated = {
                **state,
                "round": max(int(state["round"]), committed_round),
                "completed_stages": completed,
                "latest_digest": artifact_digest,
            }
            self._write_state(updated)
            return updated

    @contextmanager
    def _lock(self, filename: str, *, nonblocking: bool = False) -> Iterator[None]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            handle = (self.root / filename).open("a+b")
        except OSError as exc:
            raise RuntimeStateError(f"cannot open runtime lock: {filename}") from exc
        # Keep the lock file: unlinking it could give waiting writers a stale inode.
        with handle:
            try:
                if fcntl is None:
                    _windows_lock(handle, nonblocking=nonblocking)
                else:
                    operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
                    fcntl.flock(handle.fileno(), operation)
            except BlockingIOError as exc:
                raise RuntimeStateError("run lock is already held") from exc
            except OSError as exc:
                if nonblocking and exc.errno in {EACCES, EAGAIN}:
                    raise RuntimeStateError("run lock is already held") from exc
                raise RuntimeStateError(f"cannot acquire runtime lock: {filename}") from exc
            try:
                yield
            finally:
                if fcntl is None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _validate_fingerprint(input_fingerprint: str) -> None:
        if not isinstance(input_fingerprint, str) or not input_fingerprint:
            raise RuntimeStateError("input_fingerprint must be a non-empty string")

    def _new_state(self, input_fingerprint: str) -> dict[str, Any]:
        return {
            "version": self.STATE_VERSION,
            "run_id": uuid.uuid4().hex,
            "input_fingerprint": input_fingerprint,
            "round": 0,
            "completed_stages": {},
            "latest_digest": None,
        }

    def _read_state(self) -> dict[str, Any]:
        try:
            with self.state_path.open("rb") as handle:
                raw = handle.read()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeStateError(f"invalid state JSON: {self.state_path}") from exc
        if not isinstance(value, dict):
            raise RuntimeStateError("state JSON must be an object")
        return value

    def _validate_state(self, state: dict[str, Any]) -> None:
        required = {
            "version",
            "run_id",
            "input_fingerprint",
            "round",
            "completed_stages",
            "latest_digest",
        }
        if set(state) != required:
            raise RuntimeStateError("state JSON has an invalid schema")
        if state["version"] != self.STATE_VERSION:
            raise RuntimeStateError("unsupported state version")
        if not isinstance(state["run_id"], str) or not state["run_id"]:
            raise RuntimeStateError("run_id must be a non-empty string")
        if not isinstance(state["input_fingerprint"], str) or not state["input_fingerprint"]:
            raise RuntimeStateError("input_fingerprint must be a non-empty string")
        if not isinstance(state["round"], int) or state["round"] < 0:
            raise RuntimeStateError("state round must be a non-negative integer")
        if not isinstance(state["completed_stages"], dict):
            raise RuntimeStateError("completed_stages must be an object")
        for stage, record in state["completed_stages"].items():
            self._validate_stage(stage)
            if not isinstance(record, dict) or set(record) != {"artifact_path", "artifact_digest"}:
                raise RuntimeStateError(f"invalid record for stage {stage}")
            if not isinstance(record["artifact_path"], str) or not isinstance(
                record["artifact_digest"], str
            ):
                raise RuntimeStateError(f"invalid artifact record for stage {stage}")
            artifact = self.root / record["artifact_path"]
            self._validate_artifact_path(artifact)
            if self._sha256_file(artifact) != record["artifact_digest"]:
                raise RuntimeStateError(f"artifact digest mismatch for stage {stage}")
        latest = state["latest_digest"]
        if latest is not None and (not isinstance(latest, str) or not latest):
            raise RuntimeStateError("latest_digest must be a string or null")
        recorded_digests = {
            record["artifact_digest"]
            for record in state["completed_stages"].values()
        }
        if (not recorded_digests and latest is not None) or (
            recorded_digests and latest not in recorded_digests
        ):
            raise RuntimeStateError("latest_digest does not match a committed artifact")

    def _validate_stage(self, stage: str) -> None:
        if not isinstance(stage, str) or not self._STAGE_PATTERN.fullmatch(stage):
            raise RuntimeStateError(f"invalid stage key: {stage!r}")

    def _validate_artifact_path(self, artifact: Path) -> None:
        resolved = artifact.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeStateError("artifact must be inside the runtime root") from exc
        if not resolved.is_file():
            raise RuntimeStateError(f"missing artifact: {resolved}")

    def _round_for_stage(self, stage: str) -> int:
        if ":" not in stage:
            return 0
        return int(stage.rsplit(":", 1)[1])

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise RuntimeStateError(f"cannot read artifact: {path}") from exc
        return digest.hexdigest()

    def _write_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        except OSError as exc:
            raise RuntimeStateError(f"cannot write state: {self.state_path}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
