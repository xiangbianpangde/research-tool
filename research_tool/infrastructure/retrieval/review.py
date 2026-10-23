"""Explicit source-review records; never rewrite a run's execution verdict or final files."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..export.wiki_stage import validate_current_run


def record_review(
    root: Path, run_id: str, *, status: str, reviewer: str, reason: str,
    expected_report_sha256: str, level: str = "agent_source_review",
) -> Path:
    """Append a run/report/evidence-pinned Agent review after reading original sources.

    This API cannot assert human clinical approval. Caller must supply the expected
    report digest, and execution/final artifacts remain read-only.
    """
    root = Path(root).resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid run identity")
    if status not in {"passed", "rejected"} or level != "agent_source_review":
        raise ValueError("unsupported review status/level; no human clinical authorization")
    if not reviewer.strip() or not reason.strip():
        raise ValueError("review requires reviewer and source-check reason")
    summary, state, final = validate_current_run(root)
    if summary["run_id"] != run_id or final is None:
        raise ValueError("review must reference the current intact successful run")
    actual = hashlib.sha256((final / "report.md").read_bytes()).hexdigest()
    if actual != expected_report_sha256:
        raise ValueError("report hash changed or does not match expected identity")
    report_record = state['completed_stages']['report']
    envelope = json.loads((root / report_record['artifact_path']).read_bytes())
    for relative, expected in envelope['files'].items():
        artifact = (root / relative).resolve()
        if (not artifact.is_relative_to(root)
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected):
            raise ValueError('committed report evidence hash changed')
    snapshot = root / "runs" / run_id / "evidence-snapshot.json"
    evidence = snapshot if snapshot.is_file() else final / "tree/knowledge.json"
    evidence_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
    if (snapshot.is_file()
            and envelope['files'].get(str(snapshot.relative_to(root))) != evidence_hash):
        raise ValueError('snapshot is not committed to the report evidence hash')
    directory = root / "runs" / run_id / "reviews"
    directory.mkdir(exist_ok=True)
    payload = {"schema": "report-review/v1", "run_id": run_id,
               "report_sha256": actual, "evidence_sha256": evidence_hash,
               "evidence_path": str((snapshot if snapshot.is_file()
                                      else final / "tree/knowledge.json").relative_to(root)),
               "status": status, "level": level, "reviewer": reviewer,
               "reason": reason, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    identifier = uuid4().hex
    temporary = directory / ('.' + identifier + '.pending')
    path = directory / (identifier + '.json')
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # exclusive atomic publication, never replace a review
    finally:
        temporary.unlink(missing_ok=True)
    return path
