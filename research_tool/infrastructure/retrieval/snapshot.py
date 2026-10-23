"""Freeze a report-independent view of already committed evidence, without inventing success."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_evidence_snapshot(  # noqa: PLR0915 - one atomic snapshot binding contract
    root: Path, state: dict[str, Any], stage_key: str, *, provisional: bool = False,
) -> Path:
    """Pin the actual knowledge/merge envelope and its committed clean/raw dependencies."""
    root = Path(root).resolve()
    if (stage_key not in state['completed_stages']
            or stage_key.split(':')[0] not in {'knowledge', 'merge'}):
        raise ValueError('evidence snapshot requires a committed graph stage')
    pins: dict[str, str] = {}
    stages = {}
    for key, record in state['completed_stages'].items():
        if key == 'report' or key in {'qgate:100', 'qgate:101'}:
            continue
        if provisional and key in {'merge:100', 'inspect:100'}:
            continue
        path = root / record['artifact_path']
        data = path.read_bytes()
        if _digest(data) != record['artifact_digest']:
            raise ValueError('committed stage hash mismatch')
        payload = json.loads(data)
        if payload['run_id'] != state['run_id'] or payload['request_id'] != key:
            raise ValueError('committed stage identity mismatch')
        pins[record['artifact_path']] = record['artifact_digest']
        stages[key] = record
        for name, digest in payload['files'].items():
            path = (root / name).resolve()
            if (not path.is_relative_to(root) or not path.is_file()
                    or _digest(path.read_bytes()) != digest):
                raise ValueError('committed output hash/path mismatch')
            if name in pins and pins[name] != digest:
                raise ValueError('conflicting committed file hashes')
            pins[name] = digest
    graph_envelope = json.loads((root / stages[stage_key]['artifact_path']).read_bytes())
    graph = graph_envelope['result']['graph']
    graph_bytes = json.dumps(
        graph, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode()
    bindings = []
    for source in graph['sources']:
        clean = Path(source['path']).resolve()
        raw = clean.parent.parent / 'raw' / clean.name
        for path in [clean, raw]:
            if not path.is_relative_to(root) or str(path.relative_to(root)) not in pins:
                raise ValueError('evidence source lacks committed raw/clean identity')
        bindings.append({'source_id': source['source_id'], 'locator': source['locator'],
                         'clean_path': str(clean.relative_to(root)),
                         'raw_path': str(raw.relative_to(root)),
                         'body_sha256': source['content_sha256']})
    payload = {'schema': 'evidence-snapshot/v1', 'run_id': state['run_id'],
               'input_fingerprint': state['input_fingerprint'], 'stage_key': stage_key,
               'stage_pins': stages, 'files': pins, 'graph_sha256': _digest(graph_bytes),
               'sources': bindings, 'scope': 'committed_clean_bodies_not_missing_supplements'}
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    name = 'evidence-initial.json' if provisional else 'evidence-snapshot.json'
    path = root / 'runs' / state['run_id'] / name
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError('immutable evidence snapshot changed; use an independent run')
    else:
        fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            os.unlink(temporary)
    return path
