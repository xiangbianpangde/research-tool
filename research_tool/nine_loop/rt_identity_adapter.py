"""rt_identity_adapter — Python↔Rust subprocess adapter for the accepted
`rt-identity` deterministic core (design: docs/plan/EX-ID-01-ADAPTER-DESIGN.md,
§3/§4/§5/§6; task RT-RF-P2-ADAPTER-IMPL-01).

Contract (protocol v1):
  stdin  → request JSONL lines (UTF-8; one self-contained request per line;
           empty lines are skipped by the child)
  stdout ← one response record per non-empty input line, same order
  stderr ← safe diagnostics only (never payloads)
  exit   → 0 ok; 2 protocol-fatal; 3 child-internal

Security boundary: the child is spawned with an EMPTY environment, no
filesystem/network/DNS privileges and no secrets. Everything network-, DNS-,
LLM- and secret-related stays on the Python side.

This module is stdlib-only (CPython 3.14.6) and performs no I/O beyond the
child pipes and the caller-requested atomic result write.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any

__all__ = [
    "AdapterError", "AdapterBinaryMismatch", "AdapterTimeout",
    "AdapterProtocolFatal", "AdapterChildInternal", "AdapterContractViolation",
    "AdapterClient", "BatchResult", "PROTOCOL_VERSION",
    "write_result_atomic", "idempotency_key",
]

PROTOCOL_VERSION = 1

# Post-parse typed errors carry the request id; pre-parse records are id=null.
_POST_PARSE_CODECS = frozenset({"limit_depth", "limit_field", "limit_count"})
_KNOWN_DECISIONS = frozenset(
    {"alias", "conflict_version", "retain", "same_bytes_distinct_id"})
_STDERR_EXCERPT_MAX = 200


# --------------------------------------------------------------------------- #
# Typed faults (design §5). Exceptions carry only safe context: exit codes,
# counts and protocol-level reasons — never URL payloads or content.
# --------------------------------------------------------------------------- #
class AdapterError(Exception):
    """Base class for all adapter faults."""


class AdapterBinaryMismatch(AdapterError):
    """Configured child binary SHA-256 does not match the pinned value."""


class AdapterTimeout(AdapterError):
    """Child exceeded the batch deadline and was killed; no partial output."""

    def __init__(self, deadline_s: float):
        self.deadline_s = deadline_s
        super().__init__(f"child deadline exceeded after {deadline_s}s")


class AdapterProtocolFatal(AdapterError):
    """Child exited 2: protocol-fatal (UTF-8 / v-too-new / 8 MiB no-LF / I/O)."""

    def __init__(self, exit_code: int, stderr_excerpt: str):
        self.exit_code = exit_code
        self.stderr_excerpt = stderr_excerpt
        super().__init__(
            f"protocol fatal exit {exit_code}: {stderr_excerpt}")


class AdapterChildInternal(AdapterError):
    """Child exited 3: panic or stdout write failure."""

    def __init__(self, exit_code: int, stderr_excerpt: str):
        self.exit_code = exit_code
        self.stderr_excerpt = stderr_excerpt
        super().__init__(f"child internal exit {exit_code}: {stderr_excerpt}")


class AdapterContractViolation(AdapterError):
    """Child output violated the protocol contract; whole batch is void."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"contract violation: {reason}")


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
class BatchResult:
    """One completed batch: decoded records plus the raw child stdout bytes."""

    __slots__ = ("records", "exit_code", "stdout_bytes", "stderr_bytes")

    def __init__(self, records: list[dict[str, Any]], exit_code: int,
                 stdout_bytes: bytes, stderr_bytes: bytes):
        self.records = records
        self.exit_code = exit_code
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes

    def __len__(self) -> int:
        return len(self.records)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class AdapterClient:
    """Spawn-once-per-batch client around the pinned `rt-identity` binary.

    The binary is pinned by SHA-256: construction refuses to run when the file
    on disk does not match ``expected_sha256`` (design §3.4). The child is
    spawned with an empty environment (design §6).
    """

    def __init__(self, binary_path: str | os.PathLike[str],
                 expected_sha256: str, deadline_s: float = 30.0,
                 command: list[str] | None = None):
        self.binary_path = os.fspath(binary_path)
        self.expected_sha256 = expected_sha256
        self.deadline_s = deadline_s
        self._command = list(command) if command is not None \
            else [self.binary_path]
        self._verify_binary_sha()

    # -- binary pin -------------------------------------------------------- #
    def _verify_binary_sha(self) -> None:
        with open(self.binary_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if digest != self.expected_sha256:
            raise AdapterBinaryMismatch(
                f"child binary sha256 {digest} != pinned "
                f"{self.expected_sha256}")

    # -- encoding ---------------------------------------------------------- #
    @staticmethod
    def encode_requests(requests: list[dict[str, Any]]) -> bytes:
        """Encode requests as JSONL (one compact line each, UTF-8)."""
        out = bytearray()
        for req in requests:
            out += json.dumps(req, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            out += b"\n"
        return bytes(out)

    @staticmethod
    def _nonempty_line_count(payload: bytes) -> int:
        return sum(1 for line in payload.split(b"\n")
                   if line.strip() != b"")

    # -- execution --------------------------------------------------------- #
    def run(self, requests: list[dict[str, Any]]) -> BatchResult:
        """Encode requests, run one child batch, validate and return records."""
        return self.run_raw(self.encode_requests(requests))

    def run_raw(self, payload: bytes) -> BatchResult:
        """Run a pre-encoded JSONL payload through one child batch."""
        try:
            proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={},
                close_fds=True,
            )
        except OSError as exc:  # spawn failure is a safe, typed fault
            raise AdapterContractViolation(
                f"child spawn failed: {type(exc).__name__}") from None
        try:
            stdout, stderr = proc.communicate(input=payload,
                                              timeout=self.deadline_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AdapterTimeout(self.deadline_s) from None
        return self._interpret(proc.returncode, stdout, stderr,
                               self._nonempty_line_count(payload))

    # -- interpretation (design §5 table) ---------------------------------- #
    def _interpret(self, exit_code: int, stdout: bytes, stderr: bytes,
                   expected_lines: int) -> BatchResult:
        excerpt = self._safe_stderr(stderr)
        if exit_code == 2:
            raise AdapterProtocolFatal(2, excerpt)
        if exit_code == 3:
            raise AdapterChildInternal(3, excerpt)
        if exit_code != 0:
            raise AdapterContractViolation(
                f"unexpected child exit {exit_code}: {excerpt}")
        out_lines = stdout.split(b"\n")
        if out_lines and out_lines[-1] == b"":
            out_lines.pop()  # trailing LF is the record terminator
        records = []
        for i, line in enumerate(out_lines):
            try:
                rec = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise AdapterContractViolation(
                    f"record {i} is not valid JSON") from None
            self._validate_record(rec, i)
            records.append(rec)
        if len(records) != expected_lines:
            raise AdapterContractViolation(
                f"record count {len(records)} != non-empty request count "
                f"{expected_lines}")
        return BatchResult(records, exit_code, stdout, stderr)

    @staticmethod
    def _safe_stderr(stderr: bytes) -> str:
        text = stderr.decode("utf-8", errors="replace").strip()
        if len(text) > _STDERR_EXCERPT_MAX:
            text = text[:_STDERR_EXCERPT_MAX] + "…"
        return text

    # -- record schema validation (design §3.3) ---------------------------- #
    @staticmethod
    def _validate_record(rec: Any, index: int) -> None:
        if not isinstance(rec, dict):
            raise AdapterContractViolation(f"record {index} is not an object")
        if rec.get("v") != PROTOCOL_VERSION:
            raise AdapterContractViolation(
                f"record {index} has wrong protocol version {rec.get('v')!r}")
        if rec.get("id") is not None and not isinstance(rec.get("id"), str):
            raise AdapterContractViolation(
                f"record {index} id must be str|null")
        ok = rec.get("ok")
        if type(ok) is not bool:
            raise AdapterContractViolation(
                f"record {index} ok must be bool")
        if ok:
            identities = rec.get("identities")
            if not isinstance(identities, list):
                raise AdapterContractViolation(
                    f"record {index} identities must be a list")
            for ident in identities:
                AdapterClient._validate_identity(ident, index)
        else:
            err = rec.get("error")
            if not isinstance(err, dict) \
                    or not isinstance(err.get("code"), str) \
                    or not isinstance(err.get("safe_message"), str):
                raise AdapterContractViolation(
                    f"record {index} error must be {{code, safe_message}}")

    @staticmethod
    def _validate_identity(ident: Any, index: int) -> None:
        if not isinstance(ident, dict):
            raise AdapterContractViolation(
                f"record {index} identity is not an object")
        for field in ("input_url", "source_id", "identity_version",
                      "canonical_locator", "decision"):
            if not isinstance(ident.get(field), str):
                raise AdapterContractViolation(
                    f"record {index} identity.{field} must be str")
        if ident.get("decision") not in _KNOWN_DECISIONS:
            raise AdapterContractViolation(
                f"record {index} identity.decision unknown")
        ch = ident.get("content_sha256")
        if ch is not None and not isinstance(ch, str):
            raise AdapterContractViolation(
                f"record {index} identity.content_sha256 must be str|null")
        if not isinstance(ident.get("aliases"), list):
            raise AdapterContractViolation(
                f"record {index} identity.aliases must be a list")
        if not isinstance(ident.get("stable_external_ids"), list):
            raise AdapterContractViolation(
                f"record {index} identity.stable_external_ids must be a list")
        if not isinstance(ident.get("policy"), dict):
            raise AdapterContractViolation(
                f"record {index} identity.policy must be an object")


# --------------------------------------------------------------------------- #
# Persistence helpers (design §4: atomic write + idempotency)
# --------------------------------------------------------------------------- #
def write_result_atomic(target_path: str | os.PathLike[str],
                        data: bytes) -> None:
    """Atomically write ``data`` to ``target_path`` (same-dir tmp + replace).

    On any failure the temporary file is removed and the target is left
    unchanged, so a fault path can never leave partial output behind.
    """
    target = os.fspath(target_path)
    tmp = f"{target}.tmp-{os.getpid()}"
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


def idempotency_key(payload: bytes) -> str:
    """Deterministic idempotency key: SHA-256 over the exact request bytes.

    Same input bytes ⇒ same key ⇒ same child output (core determinism), so
    callers may dedupe on this key before spawning a batch.
    """
    return hashlib.sha256(payload).hexdigest()
