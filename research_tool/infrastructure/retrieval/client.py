"""Thin client for the ResearchCTL public CLI.

This adapter deliberately knows only the command/envelope contract.  It does
not read ResearchCTL's SQLite database or duplicate its provenance rules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


class RetrievalClientError(RuntimeError):
    """ResearchCTL could not return a usable query envelope."""


class ResearchCtlClient:
    """Call ResearchCTL without exposing its storage implementation."""

    def __init__(
        self,
        root: str | Path,
        *,
        researchctl_root: str | Path | None = None,
        python_executable: str | None = None,
        db_path: str | Path | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.researchctl_root = (
            Path(researchctl_root).expanduser().resolve()
            if researchctl_root is not None
            else None
        )
        self.python_executable = python_executable or sys.executable
        self.db_path = (
            Path(db_path).expanduser().resolve() if db_path is not None else None
        )
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        self.timeout_sec = timeout_sec

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

    def execute(self, action: str, *args: str) -> dict[str, Any]:
        """Execute one public action and parse its JSON envelope."""
        if not action or any("\x00" in value for value in (action, *args)):
            raise ValueError("action and arguments must be non-empty safe strings")
        command = self._command(action, args)
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell, explicit trusted executable
                command,
                cwd=self.researchctl_root or self.root,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RetrievalClientError(
                f"ResearchCTL action failed before returning an envelope: {action}"
            ) from exc

        stdout = completed.stdout.strip()
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RetrievalClientError(
                    f"ResearchCTL returned invalid JSON for action: {action}"
                ) from exc
            if isinstance(payload, dict):
                return payload
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
        raise RetrievalClientError(
            f"ResearchCTL action did not return an object envelope: {action}"
            + (f" ({detail[:200]})" if detail else "")
        )

    def index(self, *, semantic: bool = False) -> dict[str, Any]:
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
        return self.execute("generate-index")


__all__ = ["ResearchCtlClient", "RetrievalClientError"]
