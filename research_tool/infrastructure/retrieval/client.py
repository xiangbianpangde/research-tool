"""Thin, fail-closed client for the ResearchCTL public CLI.

This adapter never reads ResearchCTL's database.  It invokes the public CLI,
keeps machine-readable failure envelopes, and serializes refreshes for one
research-tool topic across threads and processes.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class RetrievalClientError(RuntimeError):
    """ResearchCTL did not return a usable JSON envelope."""

    def __init__(
        self,
        message: str,
        *,
        envelope: dict[str, Any] | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.envelope = envelope
        self.returncode = returncode


class ResearchCtlClient:
    """Call ResearchCTL without exposing its storage implementation."""

    _thread_locks: dict[str, threading.Lock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(
        self,
        root: str | Path,
        *,
        researchctl_root: str | Path | None = None,
        module_root: str | Path | None = None,
        python_executable: str | None = None,
        db_path: str | Path | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        if researchctl_root is not None and module_root is not None:
            raise ValueError("researchctl_root and module_root are mutually exclusive")
        self.root = Path(root).expanduser().resolve()
        explicit_module_root = researchctl_root if researchctl_root is not None else module_root
        self.researchctl_root = (
            Path(explicit_module_root).expanduser().resolve()
            if explicit_module_root is not None
            else None
        )
        self.python_executable = python_executable or sys.executable
        self.db_path = (
            Path(db_path).expanduser().resolve() if db_path is not None else None
        )
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        self.timeout_sec = float(timeout_sec)
        self.last_envelope: dict[str, Any] | None = None

    def _command(self, action: str, args: Sequence[str]) -> list[str]:
        command = [
            self.python_executable,
            "-m",
            "researchctl",
            "--layout",
            "research-tool",
            "--root",
            str(self.root),
        ]
        if self.db_path is not None:
            command.extend(("--db", str(self.db_path)))
        command.append(action)
        command.extend(args)
        return command

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.researchctl_root is not None:
            current = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                str(self.researchctl_root)
                if not current
                else os.pathsep.join((str(self.researchctl_root), current))
            )
        return env

    @staticmethod
    def _success(payload: dict[str, Any]) -> bool:
        return str(payload.get("status", "")).lower() in {"success", "ok"}

    @staticmethod
    def _failure_envelope(
        *,
        action: str,
        code: str,
        returncode: int | None = None,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "status": "error",
            "action": action,
            "error_code": code,
        }
        if returncode is not None:
            envelope["returncode"] = returncode
        return envelope

    def execute(self, action: str, *args: str) -> dict[str, Any]:
        """Execute one public action and return only a JSON object envelope.

        A valid child envelope is preserved even when the child exits non-zero;
        its status is forced to ``error`` if the child incorrectly reported
        success.  Malformed output and timeout remain exceptions, but the
        exception carries a safe non-success envelope for callers that persist
        refresh state.
        """
        if not action or not action.strip() or any(
            not isinstance(value, str) or not value.strip() or "\x00" in value
            for value in (action, *args)
        ):
            raise ValueError("action and arguments must be non-empty safe strings")
        command = self._command(action, args)
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell, explicit trusted executable
                command,
                cwd=self.researchctl_root,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            envelope = self._failure_envelope(action=action, code="timeout")
            self.last_envelope = envelope
            raise RetrievalClientError(
                f"ResearchCTL action timed out: {action}", envelope=envelope
            ) from exc
        except OSError as exc:
            envelope = self._failure_envelope(action=action, code="launch_error")
            self.last_envelope = envelope
            raise RetrievalClientError(
                f"ResearchCTL action could not be launched: {action}", envelope=envelope
            ) from exc

        stdout = (completed.stdout or "").strip()
        if not stdout:
            envelope = self._failure_envelope(
                action=action, code="empty_output", returncode=completed.returncode
            )
            self.last_envelope = envelope
            raise RetrievalClientError(
                f"ResearchCTL action returned no JSON envelope: {action}",
                envelope=envelope,
                returncode=completed.returncode,
            )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            envelope = self._failure_envelope(
                action=action, code="invalid_json", returncode=completed.returncode
            )
            self.last_envelope = envelope
            raise RetrievalClientError(
                f"ResearchCTL returned invalid JSON for action: {action}",
                envelope=envelope,
                returncode=completed.returncode,
            ) from exc
        if not isinstance(payload, dict):
            envelope = self._failure_envelope(
                action=action, code="invalid_envelope", returncode=completed.returncode
            )
            self.last_envelope = envelope
            raise RetrievalClientError(
                f"ResearchCTL action did not return an object envelope: {action}",
                envelope=envelope,
                returncode=completed.returncode,
            )

        result = dict(payload)
        if completed.returncode != 0:
            if self._success(result):
                result["status"] = "error"
            result.setdefault("error_code", "nonzero_exit")
            result.setdefault("returncode", completed.returncode)
        elif "status" not in result:
            result["status"] = "error"
            result.setdefault("error_code", "missing_status")
        self.last_envelope = result
        return result

    @classmethod
    def _thread_lock_for(cls, key: str) -> threading.Lock:
        with cls._thread_locks_guard:
            return cls._thread_locks.setdefault(key, threading.Lock())

    @contextmanager
    def _singleflight(self) -> Iterator[None]:
        """Serialize refresh calls for this topic in threads and processes."""
        key = str(self.root)
        thread_lock = self._thread_lock_for(key)
        with thread_lock:
            lock_path = self.root / ".index" / "retrieval-refresh" / f"{self.root.name}.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def index(self, *, semantic: bool = False) -> dict[str, Any]:
        with self._singleflight():
            return self.execute("index", *( ["--semantic"] if semantic else [] ))

    def query(
        self,
        *,
        entity: str | None = None,
        text: str | None = None,
        doc_type: str | None = None,
        status: str | None = None,
        raw: bool = False,
        semantic: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        args: list[str] = []
        if entity:
            args.extend(("--entity", entity))
        if text:
            args.extend(("--text", text))
        if doc_type:
            args.extend(("--doc-type", doc_type))
        if status:
            args.extend(("--status", status))
        if raw:
            args.append("--raw")
        if semantic:
            args.append("--semantic")
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be positive")
            args.extend(("--limit", str(limit)))
        return self.execute("query", *args)

    def sources(self, owner: str) -> dict[str, Any]:
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        return self.execute("sources", owner)

    def trace(self, entity: str) -> dict[str, Any]:
        if not entity.strip():
            raise ValueError("entity must be non-empty")
        return self.execute("trace", entity)

    def history(self, *, as_of: str | None = None) -> dict[str, Any]:
        args = ["--as-of", as_of] if as_of else []
        return self.execute("history", *args)

    def reconcile(self) -> dict[str, Any]:
        return self.execute("reconcile")

    def generate_index(self) -> dict[str, Any]:
        with self._singleflight():
            return self.execute("generate-index")

    def refresh(self, *, semantic: bool = False) -> dict[str, Any]:
        """Run index and navigation generation under one single-flight lease."""
        with self._singleflight():
            indexed = self.execute("index", *( ["--semantic"] if semantic else [] ))
            generated = self.execute("generate-index")
            return {"index": indexed, "navigation": generated}


__all__ = ["ResearchCtlClient", "RetrievalClientError"]
