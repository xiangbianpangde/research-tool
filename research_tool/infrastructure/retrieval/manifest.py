"""Build the ResearchCTL-compatible manifest for one nine-stage run.

The manifest is a derived retrieval projection.  It records enough stable
identity, status, source and search-funnel metadata for an external reader to
index a ``research-tool`` topic without treating the projection as evidence.
Canonical evidence remains in the run's raw, final and stage artifact files.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ...nine_loop.evidence import parse_source

RETRIEVAL_MANIFEST_FILENAME = "retrieval-manifest.json"
RETRIEVAL_MANIFEST_VERSION = 1


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _relative_path(root: Path, path: Path) -> str:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path escapes retrieval root: {path}") from exc


def _read_json(path: Path, *, required: bool = True) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _hash_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ValueError(f"cannot hash retrieval artifact: {path}") from exc


def _source_record(root: Path, raw_path: Path, source_meta: Mapping[str, Any]) -> dict[str, Any]:
    if not raw_path.is_file():
        raise ValueError(f"source artifact is missing: {raw_path}")
    try:
        markdown = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"source artifact is unreadable: {raw_path}") from exc

    parsed = parse_source(markdown)
    locator = str(source_meta.get("url") or parsed.get("locator") or "")
    if not locator:
        raise ValueError(f"source artifact has no locator: {raw_path}")
    source_id = str(parsed.get("source_id") or "")
    if not source_id:
        raise ValueError(f"source artifact has no source_id: {raw_path}")

    body = parsed.get("text", "")
    body_hash = _sha256_text(body)
    recorded_body_hash = str(source_meta.get("content_hash") or "")
    if recorded_body_hash and recorded_body_hash != body_hash:
        raise ValueError(
            f"source body hash mismatch: {raw_path} "
            f"recorded={recorded_body_hash} actual={body_hash}"
        )

    evidence_kind = str(parsed.get("evidence_kind") or "body")
    return {
        "source_id": source_id,
        "locator": locator,
        "title": str(source_meta.get("title") or parsed.get("title") or ""),
        "source_engine": str(source_meta.get("source_engine") or ""),
        "evidence_kind": evidence_kind,
        "raw_path": _relative_path(root, raw_path),
        "body_sha256": body_hash,
        "artifact_sha256": _hash_file(raw_path),
        "fetched_at": str(source_meta.get("fetched_at") or ""),
    }


def _source_files(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.glob("*.md") if path.is_file())


def _source_metadata_by_url(raw_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw_dir in raw_dirs:
        path = raw_dir / "sources.json"
        payload = _read_json(path, required=False)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                result[item["url"]] = item
    return result


def _collect_source_records(root: Path, run_root: Path) -> list[dict[str, Any]]:
    raw_dirs = [run_root / "initial" / "raw"]
    raw_dirs.extend(sorted(run_root.glob("targeted-*/raw")))
    by_url = _source_metadata_by_url(raw_dirs)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_dir in raw_dirs:
        for path in _source_files(raw_dir):
            parsed = parse_source(path.read_text(encoding="utf-8"))
            locator = str(parsed.get("locator") or "")
            record = _source_record(root, path, by_url.get(locator, {}))
            records[(record["source_id"], record["raw_path"])] = record
    return sorted(records.values(), key=lambda item: (item["source_id"], item["raw_path"]))


def _search_stats(
    root: Path,
    run_root: Path,
    source_records: list[dict[str, Any]],
) -> dict[str, int]:
    audits: dict[str, int] = {
        "planned_query_count": 0,
        "planned_request_count": 0,
        "actual_attempt_count": 0,
        "successful_hit_count": 0,
        "retained_body_source_count": sum(
            record.get("evidence_kind") == "body" for record in source_records
        ),
        "failed_request_count": 0,
    }
    raw_dirs = [run_root / "initial" / "raw"]
    raw_dirs.extend(sorted(run_root.glob("targeted-*/raw")))
    for raw_dir in raw_dirs:
        search_path = raw_dir / "search-audit.json"
        search_payload = _read_json(search_path, required=False)
        if isinstance(search_payload, dict):
            audits["planned_query_count"] += int(
                search_payload.get("planned_query_count", 0) or 0
            )
            audits["planned_request_count"] += int(
                search_payload.get("planned_request_count", 0) or 0
            )
            audits["actual_attempt_count"] += int(
                search_payload.get("actual_attempt_count", 0) or 0
            )
            audits["successful_hit_count"] += int(
                search_payload.get("successful_hit_count", 0) or 0
            )
            audits["failed_request_count"] += int(
                search_payload.get("failed_request_count", 0) or 0
            )
            continue

        # Backward-compatible fallback for runs created before search-audit.json
        # existed.  Retained evidence is still counted from source records above.
        source_payload = _read_json(raw_dir / "source-audit.json", required=False)
        if not isinstance(source_payload, dict):
            continue
        for item in source_payload.get("sources", []):
            if not isinstance(item, dict):
                continue
            audits["actual_attempt_count"] += int(item.get("attempted", 0) or 0)
            audits["successful_hit_count"] += int(item.get("hits", 0) or 0)
            audits["failed_request_count"] += int(item.get("failed", 0) or 0)
    return audits


def _stage_status(summary: Mapping[str, Any]) -> dict[str, str]:
    completed = set(summary.get("stages_completed") or [])
    skipped = set(summary.get("stages_skipped") or [])
    result: dict[str, str] = {}
    for stage in summary.get("stages") or []:
        if stage in completed:
            result[stage] = "completed"
        elif stage in skipped:
            result[stage] = "skipped"
        else:
            result[stage] = "not_completed"
    return result


def build_retrieval_manifest(topic_root: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic manifest from an already-written run summary.

    The function does not create files and does not mutate the summary.  It
    intentionally requires a run id and uses only paths below ``topic_root``.
    """
    root = Path(topic_root).resolve()
    if not isinstance(summary, Mapping):
        raise TypeError("summary must be a mapping")
    run_id = summary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("summary must contain a non-empty run_id")
    run_root = root / "runs" / run_id
    if not run_root.is_dir():
        raise ValueError(f"run root is missing: {run_root}")

    sources = _collect_source_records(root, run_root)
    search_stats = _search_stats(root, run_root, sources)
    artifacts: dict[str, str] = {}
    final = run_root / "final"
    for name, path in (
        ("report", final / "report.md"),
        ("sources", final / "sources.json"),
        ("knowledge", final / "tree" / "knowledge.json"),
    ):
        if path.is_file():
            artifacts[name] = _relative_path(root, path)

    pipeline_complete = bool(summary.get("pipeline_complete") is True)
    verdict = summary.get("verdict")
    if not isinstance(verdict, str) and verdict is not None:
        raise ValueError("summary verdict must be a string or null")

    return {
        "schema_version": RETRIEVAL_MANIFEST_VERSION,
        "producer": "research-tool",
        "topic": str(summary.get("topic") or root.name),
        "topic_slug": str(summary.get("topic_slug") or root.name),
        "run_id": run_id,
        "verdict": verdict,
        "pipeline_complete": pipeline_complete,
        "failed_stage": summary.get("failed_stage"),
        "rounds": int(summary.get("rounds", 0) or 0),
        "input_fingerprint": _read_runtime_input_fingerprint(root),
        "stages": _stage_status(summary),
        "search": search_stats,
        "artifacts": artifacts,
        "sources": sources,
    }


def _read_runtime_input_fingerprint(root: Path) -> str | None:
    state_path = root / "runtime-state.json"
    payload = _read_json(state_path, required=False)
    if not isinstance(payload, dict):
        return None
    value = payload.get("input_fingerprint")
    return value if isinstance(value, str) else None


def write_retrieval_manifest(topic_root: Path, summary: Mapping[str, Any]) -> Path:
    """Atomically write the run manifest and return its path."""
    root = Path(topic_root).resolve()
    manifest = build_retrieval_manifest(root, summary)
    destination = root / "runs" / str(manifest["run_id"]) / RETRIEVAL_MANIFEST_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    fd, temporary_name = tempfile.mkstemp(prefix=".retrieval-manifest-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


__all__ = [
    "RETRIEVAL_MANIFEST_FILENAME",
    "RETRIEVAL_MANIFEST_VERSION",
    "build_retrieval_manifest",
    "write_retrieval_manifest",
]
