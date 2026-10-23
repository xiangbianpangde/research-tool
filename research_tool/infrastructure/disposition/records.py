"""Exact read-only origin and evidence bindings for disposition decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from ...nine_loop.evidence import parse_source, verify_claim
from ..retrieval.manifest import _validate_run_record


class DispositionError(ValueError):
    """A decision, reference or registry cannot be verified; never silently adopt it."""


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def byte_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise DispositionError(message)


def strict_json(data: bytes):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate JSON property")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                DispositionError("non-finite JSON constant")
            ),
        )
    except (ValueError, UnicodeError) as exc:
        raise DispositionError("invalid disposition JSON") from exc


def safe_bytes(root: Path, relative: str) -> bytes:
    """Open each path component without following links, including root ancestors."""
    require(
        isinstance(relative, str) and relative and "\x00" not in relative, "invalid evidence path"
    )
    parts = relative.split("/")
    require(
        not Path(relative).is_absolute() and all(p not in {"", ".", ".."} for p in parts),
        "evidence path escapes root",
    )
    absolute = Path(os.path.abspath(root))
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in [*absolute.parts[1:], *parts[:-1]]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            require(stat.S_ISREG(before.st_mode), "special evidence files are forbidden")
            data = handle.read()
            after = os.fstat(handle.fileno())

        def token(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        require(token(before) == token(after), "evidence changed during read")
        return data
    except OSError as exc:
        raise DispositionError("missing or unsafe evidence file") from exc
    finally:
        os.close(fd)


def pointer(value, path):
    require(isinstance(path, str) and (path == "" or path.startswith("/")), "invalid JSON pointer")
    if not path:
        return value
    try:
        for segment in path[1:].split("/"):
            require(not re.search(r"~(?![01])", segment), "invalid JSON pointer escape")
            segment = segment.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                require(re.fullmatch(r"0|[1-9][0-9]*", segment), "invalid array index")
                value = value[int(segment)]
            else:
                value = value[segment]
        return value
    except (KeyError, IndexError, TypeError) as exc:
        raise DispositionError("JSON pointer does not resolve") from exc


def occurrence_id(reference):
    fields = (
        "root_id",
        "run_id",
        "path",
        "artifact_sha256",
        "json_pointer",
        "value_sha256",
        "run_record_sha256",
    )
    require(set(reference) == set(fields), "invalid origin reference fields")
    return "OBS-" + digest(reference)


def committed_files(root: Path, run_id: str):
    require(isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id), "invalid run ID")
    record_path = f"runs/{run_id}/run-record.json"
    record_data = safe_bytes(root, record_path)
    record = strict_json(record_data)
    try:
        require(isinstance(record, dict), "invalid canonical record")
        _validate_run_record(record, run_id)
    except ValueError as exc:
        raise DispositionError("canonical run identity/schema mismatch") from exc
    state = record.get("runtime", {})
    require(state.get("run_id") == run_id, "runtime run identity mismatch")
    files = {}
    for key, entry in state.get("completed_stages", {}).items():
        require(
            re.fullmatch(
                r"(?:collect|clean|extract|knowledge|inspect|qgate|targeted|merge|report)"
                r"(?::[0-9]+)?",
                key,
            ),
            "invalid committed stage",
        )
        expected = f"artifacts/{run_id}/{key.replace(':', '-')}.json"
        require(entry["artifact_path"] == expected, "noncanonical stage path")
        data = safe_bytes(root, expected)
        require(byte_hash(data) == entry["artifact_digest"], "stage envelope hash mismatch")
        env = strict_json(data)
        require(
            env.get("run_id") == run_id
            and env.get("request_id") == key
            and env.get("error") is None,
            "stage envelope identity mismatch",
        )
        files[expected] = byte_hash(data)
        for path, pin in env.get("files", {}).items():
            require(
                path.startswith(f"runs/{run_id}/") and (path not in files or files[path] == pin),
                "conflicting output pin",
            )
            require(byte_hash(safe_bytes(root, path)) == pin, "committed output hash mismatch")
            files[path] = pin
    return record, byte_hash(record_data), files


def validate_reference(roots, reference, *, cache=None):
    """Resolve only existing committed artifacts; arbitrary hash-pinned prose is not evidence."""
    required = {"root_id", "run_id", "path", "artifact_sha256", "run_record_sha256"}
    require(isinstance(reference, dict), "invalid reference")
    extra = {"claim_id"} if "claim_id" in reference else {"json_pointer", "value_sha256"}
    require(set(reference) == required | extra, "unknown or missing evidence reference fields")
    require(reference["root_id"] in roots, "unregistered evidence root")
    root = Path(roots[reference["root_id"]])
    cache = {} if cache is None else cache  # one bounded read only; never reused across commits
    key = (reference["root_id"], reference["run_id"])
    if key not in cache:
        cache[key] = committed_files(root, reference["run_id"])
    record, record_pin, files = cache[key]
    require(record_pin == reference["run_record_sha256"], "run record identity changed")
    path = reference["path"]
    require(files.get(path) == reference["artifact_sha256"], "artifact was not committed")
    artifact_key = (*key, path)
    if artifact_key not in cache:
        data = safe_bytes(root, path)
        require(byte_hash(data) == reference["artifact_sha256"], "artifact hash changed")
        cache[artifact_key] = strict_json(data)
    value = cache[artifact_key]
    if "claim_id" in reference:
        require(
            path == f"runs/{reference['run_id']}/final/tree/knowledge.json",
            "claim reference is not a canonical graph",
        )
        graph_key = (*artifact_key, "verified_graph")
        if graph_key not in cache:
            validate_graph(root, record, files, value)
            cache[graph_key] = True
        facts = [f for f in value["facts"] if f["claim_id"] == reference["claim_id"]]
        require(len(facts) == 1, "claim identity missing or ambiguous")
        fact = facts[0]
        sources = {s["source_id"]: s for s in value["sources"]}
        checked = verify_claim(fact, {sid: s["text"] for sid, s in sources.items()})
        require(
            checked.valid and checked.verified_span_count == len(fact["citations"]),
            "claim spans are not exact",
        )
        require(
            all(c["locator"] == sources[c["source_id"]]["locator"] for c in fact["citations"]),
            "citation source identity differs",
        )
        return fact
    require(
        "json_pointer" in reference and "value_sha256" in reference,
        "reference requires an exact value pointer",
    )
    value = pointer(value, reference["json_pointer"])
    require(digest(value) == reference["value_sha256"], "original record hash differs")
    return value


def validate_graph(root, record, files, graph):
    """Final claims must derive from pinned graph stages and pinned clean body bytes."""
    matches = []
    for key, entry in record["runtime"]["completed_stages"].items():
        if key.split(":")[0] in {"knowledge", "merge"}:
            data = safe_bytes(root, entry["artifact_path"])
            require(
                byte_hash(data) == entry["artifact_digest"], "graph stage envelope hash changed"
            )
            env = strict_json(data)
            matches.append(env.get("result", {}).get("graph") == graph)
    require(any(matches), "final graph is not a committed knowledge/merge result")
    sources = graph["sources"]
    require(len({s["source_id"] for s in sources}) == len(sources), "ambiguous graph sources")
    require(
        len({f["claim_id"] for f in graph["facts"]}) == len(graph["facts"]), "ambiguous graph facts"
    )
    for source in sources:
        path = Path(os.path.abspath(source["path"]))
        require(path.is_relative_to(root), "graph clean source escapes root")
        relative = path.relative_to(root).as_posix()
        require("/clean/" in relative and relative in files, "source lacks committed clean body")
        data = safe_bytes(root, relative)
        require(byte_hash(data) == files[relative], "clean body hash changed")
        parsed = parse_source(data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
        require(
            all(parsed[k] == source[k] for k in ["source_id", "locator", "text", "content_sha256"]),
            "graph source differs from committed clean body",
        )


def audit_observations(roots, root_id, run_id, expected_hash):
    require(root_id in roots, "unregistered evidence root")
    root = Path(roots[root_id])
    record, record_pin, files = committed_files(root, run_id)
    path = f"runs/{run_id}/final/evidence-audit.json"
    data = safe_bytes(root, path)
    require(
        files.get(path) == expected_hash == byte_hash(data),
        "audit was not committed or hash differs",
    )
    audit = strict_json(data)
    require(
        isinstance(audit.get("unresolved_findings"), list)
        and isinstance(audit.get("known_limitations"), list),
        "audit lacks original observations",
    )
    result = []
    for name, kind in [("unresolved_findings", "finding"), ("known_limitations", "limitation")]:
        for index, value in enumerate(audit[name]):
            ref = {
                "root_id": root_id,
                "run_id": run_id,
                "path": path,
                "artifact_sha256": expected_hash,
                "json_pointer": f"/{name}/{index}",
                "value_sha256": digest(value),
                "run_record_sha256": record_pin,
            }
            oid = occurrence_id(ref)
            result.append(
                {
                    "occurrence_id": oid,
                    "kind": kind,
                    "origin": ref,
                    "original": value,
                    "finding_id": value.get("finding_id") if isinstance(value, dict) else None,
                    "required": True,
                    "execution_verdict": record["verdict"],
                }
            )
    return result
