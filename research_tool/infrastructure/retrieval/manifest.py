"""Build a strict ResearchCTL manifest for one persisted nine-stage run.

The manifest is a derived projection.  ``run-record.json`` is the canonical
run snapshot and is the only status source used here; the topic-level
``run-summary.json`` is intentionally not consulted when projecting history.

Hash conventions are deliberately split:

* values in ``manifest.files`` and manifest artifact/source hash fields use
  ``sha256:<64 hex>``;
* ``run-record.files`` and the runtime state's ``artifact_digest`` retain the
  existing bare 64-hex representation.
* evidence body hashes are calculated from ``parse_source``'s body text, so
  Python's Unicode/newline normalization is identical to the evidence engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ...nine_loop.evidence import parse_source

RETRIEVAL_MANIFEST_FILENAME = "retrieval-manifest.json"
RUN_RECORD_FILENAME = "run-record.json"
RETRIEVAL_MANIFEST_VERSION = 1

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_VERDICTS = {"STOP_SUCCESS", "STOP_ERROR", "STOP_BUDGET", "STOP_CANCELLED"}
_CONTROL_FILES = {"sources.json", "source-audit.json", "search-audit.json"}
_FINAL_FILES: tuple[tuple[str, str], ...] = (
    ("report", "final/report.md"),
    ("sources", "final/sources.json"),
    ("main_table", "final/tree/00-主表.md"),
    ("knowledge", "final/tree/knowledge.json"),
)


def _bare_sha256(value: str, *, field: str = "hash") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a SHA-256 string")
    match = _SHA256_RE.fullmatch(value.strip().lower())
    if match is None:
        raise ValueError(f"{field} must be bare64hex or sha256:64hex")
    return match.group(1)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_hash(data: bytes) -> str:
    return "sha256:" + _sha256_bytes(data)


def _manifest_text_hash(text: str) -> str:
    return _manifest_hash(text.encode("utf-8"))


def _relative_path(root: Path, path: Path) -> str:
    """Return a topic-root-relative POSIX path and reject escapes."""
    resolved_root = root.resolve()
    resolved_path = path.expanduser().resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path escapes retrieval root: {path}") from exc


def _resolve_path(root: Path, raw: str | Path, *, base: Path | None = None) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base or root) / path
    return path.resolve()


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


def _hash_file_manifest(path: Path) -> str:
    return "sha256:" + _hash_file(path)


def _metadata(markdown: str) -> dict[str, str]:
    """Read the small metadata subset used by retrieval projection.

    ``parse_source`` is the authority for the body and source identity.  This
    helper only recovers optional provenance labels which the parser
    intentionally does not expose.
    """
    result: dict[str, str] = {}
    for line in markdown.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("<!--") and stripped.endswith("-->")):
            break
        item = stripped[4:-3].strip()
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _source_tier(source_meta: Mapping[str, Any], parsed: Mapping[str, Any]) -> str:
    explicit = source_meta.get("source_tier") or source_meta.get("tier")
    if explicit:
        return str(explicit)
    # These are stable labels for existing producer engines, not quality
    # claims.  Explicit persisted metadata always wins above.
    engine = str(source_meta.get("source_engine") or "").lower()
    if engine == "official":
        return "official"
    if engine in {"expert_seed", "expert", "curated"}:
        return "curated"
    if engine == "secondary":
        return "secondary"
    if str(parsed.get("evidence_kind") or "body") != "body":
        return "discovery"
    return "search"


def _source_metadata(raw_dirs: list[Path]) -> tuple[dict[str, dict[str, Any]], list[Path]]:
    """Index persisted source metadata and compatible absolute path entries."""
    by_key: dict[str, dict[str, Any]] = {}
    paths: list[Path] = []
    for raw_dir in raw_dirs:
        payload = _read_json(raw_dir / "sources.json", required=False)
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            for key in (item.get("url"), item.get("locator"), item.get("source_id")):
                if isinstance(key, str) and key:
                    by_key[key] = item
            raw_path = item.get("raw_path") or item.get("path")
            if isinstance(raw_path, str) and raw_path:
                paths.append(_resolve_path(raw_dir, raw_path, base=raw_dir))
    return by_key, paths


def _raw_dirs(run_root: Path) -> list[Path]:
    result = [run_root / "initial" / "raw"]
    result.extend(sorted(run_root.glob("targeted-*/raw")))
    return [path for path in result if path.is_dir()]


def _raw_candidates(raw_dirs: list[Path], metadata_paths: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for raw_dir in raw_dirs:
        for path in raw_dir.rglob("*"):
            if not path.is_file() or path.name in _CONTROL_FILES:
                continue
            if any(part in {"_originals", "_pdfs"} for part in path.relative_to(raw_dir).parts):
                continue
            paths.add(path.resolve())
    for path in metadata_paths:
        if path.is_file() and path.name not in _CONTROL_FILES:
            paths.add(path.resolve())
    return sorted(paths, key=lambda item: item.as_posix())


def _source_record(
    root: Path,
    raw_path: Path,
    source_meta: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not raw_path.is_file():
        return None
    try:
        # Keep artifact_sha256 on original bytes, but use the same universal
        # newline text boundary as the evidence parser and pipeline reader.
        raw_bytes = raw_path.read_bytes()
        markdown = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    parsed = parse_source(markdown)
    metadata = _metadata(markdown)
    locator = str(
        parsed.get("locator")
        or source_meta.get("url")
        or source_meta.get("locator")
        or metadata.get("source")
        or ""
    )
    if not locator:
        return None
    source_id = str(source_meta.get("source_id") or parsed.get("source_id") or "")
    if not source_id:
        return None

    # parse_source is the canonical newline/Unicode boundary.  Its
    # content_sha256 is bare; manifest body hashes are prefixed.
    body = str(parsed.get("text") or "")
    body_hash = _bare_sha256(
        str(parsed.get("content_sha256") or _sha256_bytes(body.encode("utf-8"))),
        field="source body hash",
    )
    recorded_body_hash = source_meta.get("content_hash") or source_meta.get("body_hash")
    if recorded_body_hash:
        if _bare_sha256(str(recorded_body_hash), field="recorded source body hash") != body_hash:
            raise ValueError(
                f"source body hash mismatch: {raw_path} "
                f"recorded={recorded_body_hash} actual=sha256:{body_hash}"
            )

    evidence_kind = str(
        parsed.get("evidence_kind")
        or source_meta.get("evidence_kind")
        or metadata.get("evidence_kind")
        or "body"
    )
    source_engine = str(source_meta.get("source_engine") or metadata.get("source_engine") or "")
    relative = _relative_path(root, raw_path)
    artifact_hash = _manifest_hash(raw_bytes)
    body_hash_prefixed = "sha256:" + body_hash
    return {
        "source_id": source_id,
        "locator": locator,
        "title": str(
            source_meta.get("title") or parsed.get("title") or metadata.get("title") or ""
        ),
        "source_engine": source_engine,
        "evidence_kind": evidence_kind,
        "source_tier": _source_tier(
            {**dict(source_meta), "source_engine": source_engine},
            {**parsed, "evidence_kind": evidence_kind},
        ),
        "raw_path": relative,
        "path": relative,
        "body_sha256": body_hash_prefixed,
        "body_hash": body_hash_prefixed,
        "content_sha256": body_hash,
        "artifact_sha256": artifact_hash,
        "artifact_hash": artifact_hash,
        "fetched_at": str(source_meta.get("fetched_at") or metadata.get("fetched") or ""),
    }


def _collect_source_records(root: Path, run_root: Path) -> list[dict[str, Any]]:
    raw_dirs = _raw_dirs(run_root)
    by_key, metadata_paths = _source_metadata(raw_dirs)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in _raw_candidates(raw_dirs, metadata_paths):
        try:
            markdown = path.read_text(encoding="utf-8")
            parsed = parse_source(markdown)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        key = str(parsed.get("locator") or parsed.get("source_id") or "")
        meta = by_key.get(key, {})
        record = _source_record(root, path, meta)
        if record is None:
            continue
        records[(record["source_id"], record["raw_path"])] = record
    return sorted(records.values(), key=lambda item: (item["source_id"], item["raw_path"]))


def _audit_value(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return 0


def _search_stats(
    run_root: Path,
    source_records: list[dict[str, Any]],
) -> dict[str, int]:
    """Aggregate only persisted audit counters, without counting call details twice."""
    stats = {
        "planned_query_count": 0,
        "planned_request_count": 0,
        "actual_attempt_count": 0,
        "successful_hit_count": 0,
        "retained_body_source_count": 0,
        "failed_request_count": 0,
    }
    records_by_dir: dict[str, list[dict[str, Any]]] = {}
    for source in source_records:
        path = Path(source["raw_path"])
        records_by_dir.setdefault(path.parent.as_posix(), []).append(source)

    for raw_dir in _raw_dirs(run_root):
        search_payload = _read_json(raw_dir / "search-audit.json", required=False)
        source_payload = _read_json(raw_dir / "source-audit.json", required=False)
        if isinstance(search_payload, dict):
            # Top-level counters are authoritative.  If an older persisted
            # audit has only calls, use those once rather than summing both.
            calls = search_payload.get("calls")
            if all(key in search_payload for key in (
                "planned_query_count",
                "planned_request_count",
                "actual_attempt_count",
                "successful_hit_count",
                "failed_request_count",
            )):
                for key in (
                    "planned_query_count",
                    "planned_request_count",
                    "actual_attempt_count",
                    "successful_hit_count",
                    "failed_request_count",
                ):
                    stats[key] += _audit_value(search_payload, key)
            elif isinstance(calls, list):
                stats["planned_query_count"] += sum(
                    len(call.get("queries", []))
                    for call in calls
                    if isinstance(call, dict) and isinstance(call.get("queries", []), list)
                )
                for key in (
                    "planned_request_count", "actual_attempt_count",
                    "successful_hit_count", "failed_request_count",
                ):
                    stats[key] += sum(
                        _audit_value(call, key)
                        for call in calls
                        if isinstance(call, dict)
                    )
        elif isinstance(source_payload, dict):
            # source-audit is the persisted fallback for older runs.  It has
            # no planned-search counters, so those remain zero rather than
            # being inferred from retained files.
            for item in source_payload.get("sources", []):
                if not isinstance(item, dict):
                    continue
                stats["actual_attempt_count"] += _audit_value(item, "attempted")
                stats["successful_hit_count"] += _audit_value(item, "hits")
                stats["failed_request_count"] += _audit_value(item, "failed")

        if isinstance(source_payload, dict):
            # ``retained`` counts persisted source records, while the body
            # subset is determined only from the corresponding persisted raw
            # files.  This keeps discovery/snippet records out of the body
            # funnel and never substitutes a source scan for missing audit data.
            raw_key = _relative_path(run_root.parent.parent, raw_dir)
            body_records = records_by_dir.get(raw_key, [])
            stats["retained_body_source_count"] += sum(
                source.get("evidence_kind") == "body" for source in body_records
            )
    return stats


def _stage_status(record: Mapping[str, Any]) -> dict[str, str]:
    completed = set(record.get("stages_completed") or [])
    skipped = set(record.get("stages_skipped") or [])
    result: dict[str, str] = {}
    for stage in record.get("stages") or []:
        if stage in completed:
            result[stage] = "completed"
        elif stage in skipped:
            result[stage] = "skipped"
        else:
            result[stage] = "not_completed"
    return result


def _validate_run_record(record: Mapping[str, Any], run_id: str) -> None:
    required = {
        "version", "run_id", "topic", "topic_slug", "verdict", "pipeline_complete",
        "started_at", "finished_at", "input_fingerprint", "output_contract", "stages",
        "stages_completed", "stages_skipped", "failed_stage", "files", "runtime",
    }
    if set(record) != required:
        raise ValueError("run-record.json has an invalid schema")
    if record.get("version") != 1 or record.get("run_id") != run_id:
        raise ValueError("run-record.json version or run_id is invalid")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must be exactly 32 lowercase hexadecimal characters")
    if record.get("verdict") not in _VERDICTS:
        raise ValueError("run-record verdict is invalid")
    if not isinstance(record.get("pipeline_complete"), bool):
        raise ValueError("run-record pipeline_complete must be boolean")
    if record.get("output_contract") != "nine-stage":
        raise ValueError("run-record output contract is invalid")
    if list(record.get("stages") or []) != [
        "collect", "clean", "extract", "knowledge", "inspect",
        "targeted", "merge", "qgate", "report",
    ]:
        raise ValueError("run-record stages are invalid")
    if (
        not isinstance(record.get("stages_completed"), list)
        or not isinstance(record.get("stages_skipped"), list)
    ):
        raise ValueError("run-record stage lists are invalid")
    _bare_sha256(str(record.get("input_fingerprint")), field="input_fingerprint")
    if not isinstance(record.get("files"), dict) or not isinstance(record.get("runtime"), dict):
        raise ValueError("run-record files/runtime are invalid")


def _read_run_record(root: Path, summary: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    if not isinstance(summary, Mapping):
        raise TypeError("summary must be a mapping")
    run_id = summary.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("summary must contain a valid run_id")
    record_path = root / "runs" / run_id / RUN_RECORD_FILENAME
    record = _read_json(record_path)
    if not isinstance(record, dict):
        raise ValueError("run-record.json must contain an object")
    _validate_run_record(record, run_id)
    return record_path, record


def _canonical_final_files(root: Path, run_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name, relative in _FINAL_FILES:
        path = run_root / relative
        if not path.is_file():
            raise ValueError(f"successful run is missing final artifact: {path}")
        result[name] = path
    return result


def _source_indexes(
    root: Path, run_root: Path
) -> tuple[dict[tuple[str, str], list[Path]], dict[str, list[Path]]]:
    by_identity: dict[tuple[str, str], list[Path]] = {}
    by_id: dict[str, list[Path]] = {}
    for clean_dir in [run_root / "initial" / "clean", *sorted(run_root.glob("targeted-*/clean"))]:
        if not clean_dir.is_dir():
            continue
        for path in sorted(clean_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                parsed = parse_source(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            sid = str(parsed.get("source_id") or "")
            locator = str(parsed.get("locator") or "")
            if sid:
                by_id.setdefault(sid, []).append(path)
            if sid or locator:
                by_identity.setdefault((sid, locator), []).append(path)
    return by_identity, by_id


def _raw_index(source_records: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in source_records:
        key = (str(item.get("source_id", "")), str(item.get("locator", "")))
        result.setdefault(key, []).append(item)
    return result


def _citation_sources(
    root: Path,
    run_root: Path,
    source_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    final_sources_path = run_root / "final" / "sources.json"
    if not final_sources_path.is_file():
        return []
    payload = _read_json(final_sources_path)
    if not isinstance(payload, list):
        raise ValueError("final sources.json must contain an array")
    by_identity, by_id = _source_indexes(root, run_root)
    raw_by_identity = _raw_index(source_records)
    result: list[dict[str, Any]] = []
    for source in payload:
        if not isinstance(source, dict):
            raise ValueError("final source record must be an object")
        source_id = str(source.get("source_id") or "")
        locator = str(source.get("locator") or "")
        if not source_id or not locator:
            raise ValueError("final source record lacks source_id or locator")
        clean_candidates: list[Path] = []
        declared = source.get("path")
        if isinstance(declared, str) and declared:
            declared_path = _resolve_path(root, declared, base=root)
            if declared_path.is_file() and declared_path.is_relative_to(run_root):
                clean_candidates.append(declared_path)
        clean_candidates.extend(by_identity.get((source_id, locator), []))
        clean_candidates.extend(by_id.get(source_id, []))
        clean_path = next((path for path in clean_candidates if path.is_file()), None)

        raw_candidates = raw_by_identity.get((source_id, locator), [])
        raw_path = None
        if clean_path is not None:
            candidate = clean_path.parent.parent / "raw" / clean_path.name
            if candidate.is_file():
                raw_path = candidate
        if raw_path is None and raw_candidates:
            raw_path = root / raw_candidates[0]["raw_path"]
        if clean_path is None or raw_path is None or not raw_path.is_file():
            raise ValueError(
                f"cannot map final citation source to clean/raw artifacts: {source_id}"
            )

        clean_parsed = parse_source(clean_path.read_text(encoding="utf-8"))
        raw_parsed = parse_source(raw_path.read_text(encoding="utf-8"))
        clean_hash = _bare_sha256(str(clean_parsed["content_sha256"]), field="clean citation hash")
        raw_hash = _bare_sha256(str(raw_parsed["content_sha256"]), field="raw citation hash")
        result.append({
            "source_id": source_id,
            "locator": locator,
            "clean_path": _relative_path(root, clean_path),
            "raw_path": _relative_path(root, raw_path),
            "body_sha256": "sha256:" + clean_hash,
            "clean_hash": "sha256:" + clean_hash,
            "raw_hash": "sha256:" + raw_hash,
            "clean_body_sha256": "sha256:" + clean_hash,
            "raw_body_sha256": "sha256:" + raw_hash,
            "clean_artifact_sha256": _hash_file_manifest(clean_path),
            "raw_artifact_sha256": _hash_file_manifest(raw_path),
        })
    return sorted(result, key=lambda item: (item["source_id"], item["locator"]))


def _collect_manifest_files(
    root: Path, run_root: Path, record: Mapping[str, Any]
) -> dict[str, str]:
    files: dict[str, str] = {}
    candidates: set[Path] = set()
    # Runtime state is the authority for committed stage envelopes.  Do not
    # sweep every file below artifacts/<run_id>: an uncommitted scratch file
    # must not silently become part of the retrieval manifest.
    for item in record["runtime"].get("completed_stages", {}).values():
        if isinstance(item, dict) and isinstance(item.get("artifact_path"), str):
            candidates.add(_resolve_path(root, item["artifact_path"]))

    for envelope_path in sorted(candidates, key=lambda path: path.as_posix()):
        relative = _relative_path(root, envelope_path)
        files[relative] = _manifest_hash(envelope_path.read_bytes())
        try:
            envelope = _read_json(envelope_path)
        except ValueError:
            continue
        if not isinstance(envelope, dict):
            continue
        outputs = envelope.get("files", {})
        if not isinstance(outputs, dict):
            continue
        for raw_path in outputs:
            if not isinstance(raw_path, str):
                continue
            output_path = _resolve_path(root, raw_path)
            if not output_path.is_file():
                raise ValueError(f"stage envelope output is missing: {output_path}")
            files[_relative_path(root, output_path)] = _manifest_hash(output_path.read_bytes())

    record_path = run_root / RUN_RECORD_FILENAME
    if not record_path.is_file():
        raise ValueError(f"run-record is missing: {record_path}")
    files[_relative_path(root, record_path)] = _manifest_hash(record_path.read_bytes())
    return dict(sorted(files.items()))


def build_retrieval_manifest(topic_root: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    """Build a manifest from the canonical record for exactly one run."""
    root = Path(topic_root).expanduser().resolve()
    record_path, record = _read_run_record(root, summary)
    run_root = record_path.parent
    source_records = _collect_source_records(root, run_root)
    files = _collect_manifest_files(root, run_root, record)

    artifacts: dict[str, str] = {}
    artifact_hashes: dict[str, str] = {}
    citation_sources: list[dict[str, Any]] = []
    if record["verdict"] == "STOP_SUCCESS" and record["pipeline_complete"]:
        final_files = _canonical_final_files(root, run_root)
        for name, path in final_files.items():
            artifacts[name] = _relative_path(root, path)
            artifact_hashes[name] = _hash_file_manifest(path)
        citation_sources = _citation_sources(root, run_root, source_records)

    return {
        "schema": RETRIEVAL_MANIFEST_VERSION,
        "schema_version": RETRIEVAL_MANIFEST_VERSION,
        "producer": "research-tool",
        "topic": record["topic"],
        "topic_slug": record["topic_slug"],
        "run_id": record["run_id"],
        "verdict": record["verdict"],
        "pipeline_complete": record["pipeline_complete"],
        "failed_stage": record["failed_stage"],
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "input_fingerprint": _bare_sha256(record["input_fingerprint"], field="input_fingerprint"),
        "output_contract": record["output_contract"],
        "stages": _stage_status(record),
        "stages_completed": list(record["stages_completed"]),
        "stages_skipped": list(record["stages_skipped"]),
        "search": _search_stats(run_root, source_records),
        "artifacts": artifacts,
        "artifact_hashes": artifact_hashes,
        "files": files,
        "record_path": _relative_path(root, record_path),
        "record_sha256": _hash_file_manifest(record_path),
        "sources": source_records,
        "citation_sources": citation_sources,
    }


def write_retrieval_manifest(topic_root: Path, summary: Mapping[str, Any]) -> Path:
    """Atomically write the per-run manifest and return its path."""
    root = Path(topic_root).expanduser().resolve()
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
    "RUN_RECORD_FILENAME",
    "build_retrieval_manifest",
    "write_retrieval_manifest",
]
