"""Build immutable, content-addressed packages for unverified Wiki intake.

This module never writes an Obsidian Vault.  It snapshots a research result into
an isolated package that a separate governed draft importer may validate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_FILES = 5_000
MAX_FILE_BYTES = 8_000_000
MAX_PACKAGE_BYTES = 100_000_000
SOURCE_RE = re.compile(r"^<!--\s*source:\s*(https?://[^\s]+)\s*-->$", re.MULTILINE)
FETCHED_RE = re.compile(r"^<!--\s*fetched:\s*([^\s]+)\s*-->$", re.MULTILINE)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai", re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("github", re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("bearer", re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.I)),
    ("assigned_secret", re.compile(
        rb"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret)"
        rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}", re.I,
    )),
)


class PackageStageError(ValueError):
    """The source package failed a closed safety or integrity check."""


@dataclass(frozen=True)
class StagePackage:
    package_id: str
    package_hash: str
    package_path: Path
    manifest: dict[str, Any]
    already_exists: bool


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_source(source: Path) -> Path:
    lexical = Path(os.path.abspath(source.expanduser()))
    try:
        info = lexical.lstat()
    except OSError as exc:
        raise PackageStageError("source package does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PackageStageError("source package must be a regular directory, not a symlink")
    return lexical.resolve(strict=True)


def _files(source: Path) -> list[tuple[Path, bytes, os.stat_result]]:
    found: list[tuple[Path, bytes, os.stat_result]] = []
    total = 0
    for root, directories, names in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *names]:
            candidate = root_path / name
            if candidate.is_symlink():
                raise PackageStageError("symlink entries are forbidden in research packages")
        for name in sorted(names):
            candidate = root_path / name
            before = candidate.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise PackageStageError("special files are forbidden in research packages")
            if before.st_size > MAX_FILE_BYTES:
                raise PackageStageError("research artifact exceeds the per-file size limit")
            data = candidate.read_bytes()
            after = candidate.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ) or len(data) != before.st_size:
                raise PackageStageError("research artifact changed during snapshot")
            for rule, pattern in SECRET_PATTERNS:
                if pattern.search(data):
                    raise PackageStageError(f"secret-like content rejected (rule: {rule})")
            total += len(data)
            if total > MAX_PACKAGE_BYTES:
                raise PackageStageError("research package exceeds the total size limit")
            relative = candidate.relative_to(source)
            found.append((relative, data, before))
            if len(found) > MAX_FILES:
                raise PackageStageError("research package has too many files")
    if not found:
        raise PackageStageError("research package contains no artifacts")
    return sorted(found, key=lambda item: item[0].as_posix())


def _source_records(files: list[tuple[Path, bytes, os.stat_result]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for relative, data, _ in files:
        if relative.suffix.lower() != ".md":
            continue
        text = data.decode("utf-8", errors="replace")
        source_match = SOURCE_RE.search(text)
        if not source_match:
            continue
        fetched_match = FETCHED_RE.search(text)
        content_hash = _digest(data)
        url = source_match.group(1)
        fetched_at = fetched_match.group(1) if fetched_match else "unknown"
        stable_id = "src_" + _digest(_canonical({"url": url, "fetchedAt": fetched_at}))[:24]
        records.append({
            "sourceId": stable_id,
            "url": url,
            "fetchedAt": fetched_at,
            "sha256": content_hash,
            "archiveRef": f"research-archive://sha256/{content_hash}",
            "artifactPath": relative.as_posix(),
        })
    return sorted(records, key=lambda item: (item["sourceId"], item["artifactPath"]))


def _intake_documents(
    core: dict[str, Any], files: list[tuple[Path, bytes, os.stat_result]]
) -> dict[str, bytes]:
    report = next((data for relative, data, _ in files if relative.as_posix() == "report.md"), b"")
    classification = core["classification"]
    lines = [
        "# Research source manifest (unverified)",
        "",
        "verification: unverified",
        "promotion: forbidden",
        f"privacy: {classification['privacy']}",
        f"confidentiality: {classification['confidentiality']}",
        f"copyright: {classification['copyright']}",
        "isolation_required: true",
        "",
        "## Traceable sources",
        "",
    ]
    for item in core["sources"]:
        lines.extend([
            f"- source_id: `{item['sourceId']}`",
            f"  - url: {item['url']}",
            f"  - fetched_at: {item['fetchedAt']}",
            f"  - sha256: `{item['sha256']}`",
            f"  - archive_ref: `{item['archiveRef']}`",
        ])
    source_manifest = ("\n".join(lines) + "\n").encode()
    report_doc = (
        b"# Research report (unverified)\n\nverification: unverified\n\n"
        + report
        + b"\n\n---\n\n"
        + source_manifest
    )
    return {
        "report.md": report_doc,
        "source-manifest.md": source_manifest,
    }


def _manifest(
    source: Path,
) -> tuple[dict[str, Any], list[tuple[Path, bytes, os.stat_result]], dict[str, bytes]]:
    files = _files(source)
    artifacts = []
    for relative, data, _ in files:
        content_hash = _digest(data)
        artifacts.append({
            "path": relative.as_posix(),
            "sha256": content_hash,
            "bytes": len(data),
            "archivePath": f"archive/{content_hash}.blob",
        })
    sources = _source_records(files)
    core: dict[str, Any] = {
        "schemaVersion": 1,
        "producer": "research-tool/wiki-stage-p1",
        "sourceSlug": source.name,
        "verification": "unverified",
        "target": "isolated_research_draft",
        "promotion": "forbidden",
        "classification": {
            "privacy": "unknown",
            "confidentiality": "unknown",
            "copyright": "unknown",
            "isolationRequired": True,
        },
        "artifacts": artifacts,
        "sources": sources,
        "sourceIds": sorted({item["sourceId"] for item in sources}),
        "sections": {
            "report": (
                "report.md" if any(item["path"] == "report.md" for item in artifacts) else None
            ),
            "knowledgeTree": sorted(
                item["path"] for item in artifacts if item["path"].startswith("tree/")
            ),
            "evidence": sorted(item["artifactPath"] for item in sources),
        },
    }
    intake_documents = _intake_documents(core, files)
    core["intakeDocuments"] = [
        {"path": name, "sha256": _digest(data), "bytes": len(data)}
        for name, data in sorted(intake_documents.items())
    ]
    package_hash = _digest(_canonical(core))
    manifest = {**core, "packageId": f"rp_{package_hash[:32]}", "packageHash": package_hash}
    return manifest, files, intake_documents


def plan_stage_package(source: Path, destination: Path) -> dict[str, Any]:
    """Return a deterministic plan without creating the destination."""
    del destination  # the dry-run must not inspect or create a mutable target
    manifest, files, _ = _manifest(_safe_source(source))
    return {
        "packageId": manifest["packageId"],
        "packageHash": manifest["packageHash"],
        "verification": manifest["verification"],
        "target": manifest["target"],
        "files": len(files),
        "sources": manifest["sources"],
        "classification": manifest["classification"],
        "writes": [],
    }


def _verify_existing(package_path: Path, manifest: dict[str, Any]) -> None:
    try:
        existing = json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageStageError("existing content-addressed package is invalid") from exc
    if existing != manifest:
        raise PackageStageError("content-addressed package collision")
    core = {
        key: value
        for key, value in existing.items()
        if key not in {"packageId", "packageHash"}
    }
    if _digest(_canonical(core)) != existing["packageHash"]:
        raise PackageStageError("existing package integrity check failed")
    expected_archives = set()
    for artifact in existing["artifacts"]:
        relative = Path(artifact["archivePath"])
        expected_archives.add(relative.name)
        target = package_path / relative
        try:
            info = target.lstat()
            data = target.read_bytes()
        except OSError as exc:
            raise PackageStageError("existing package integrity check failed") from exc
        if not stat.S_ISREG(info.st_mode) or target.is_symlink() or len(data) != artifact["bytes"] \
                or _digest(data) != artifact["sha256"]:
            raise PackageStageError("existing package integrity check failed")
    archive_root = package_path / "archive"
    try:
        actual_archives = {item.name for item in archive_root.iterdir() if item.is_file()}
    except OSError as exc:
        raise PackageStageError("existing package integrity check failed") from exc
    if actual_archives != expected_archives:
        raise PackageStageError("existing package integrity check failed")
    for document in existing["intakeDocuments"]:
        target = package_path / document["path"]
        try:
            info = target.lstat()
            data = target.read_bytes()
        except OSError as exc:
            raise PackageStageError("existing package integrity check failed") from exc
        if not stat.S_ISREG(info.st_mode) or target.is_symlink() or len(data) != document["bytes"] \
                or _digest(data) != document["sha256"]:
            raise PackageStageError("existing package integrity check failed")


def build_stage_package(source: Path, destination: Path) -> StagePackage:
    """Atomically snapshot source artifacts under a content-addressed directory."""
    source = _safe_source(source)
    manifest, files, intake_documents = _manifest(source)
    destination = Path(os.path.abspath(destination.expanduser()))
    package_path = destination / manifest["packageId"]
    if package_path.exists():
        _verify_existing(package_path, manifest)
        return StagePackage(
            manifest["packageId"], manifest["packageHash"], package_path, manifest, True
        )

    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".wiki-stage-", dir=destination))
    try:
        artifact_root = temporary / "archive"
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for _relative, data, _ in files:
            target = artifact_root / f"{_digest(data)}.blob"
            if not target.exists():
                target.write_bytes(data)
                target.chmod(0o444)
        for name, data in intake_documents.items():
            target = temporary / name
            target.write_bytes(data)
            target.chmod(0o444)
        manifest_path = temporary / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest) + b"\n")
        manifest_path.chmod(0o444)
        directories = (item for item in temporary.rglob("*") if item.is_dir())
        for directory in sorted(directories, reverse=True):
            directory.chmod(0o555)
        temporary.chmod(0o555)
        try:
            os.rename(temporary, package_path)
        except FileExistsError:
            _verify_existing(package_path, manifest)
            shutil.rmtree(temporary)
            return StagePackage(
                manifest["packageId"], manifest["packageHash"], package_path, manifest, True
            )
    except BaseException:
        if temporary.exists():
            for item in temporary.rglob("*"):
                if item.is_dir():
                    item.chmod(0o700)
                else:
                    item.chmod(0o600)
            temporary.chmod(0o700)
            shutil.rmtree(temporary)
        raise
    return StagePackage(
        manifest["packageId"], manifest["packageHash"], package_path, manifest, False
    )
