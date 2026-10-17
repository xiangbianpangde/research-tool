"""Public contract tests for the durable nine-loop runtime state."""

from __future__ import annotations

import hashlib
import errno
import builtins
import runpy
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from research_tool.nine_loop.runtime_state import RuntimeState, RuntimeStateError


def _artifact(root: Path, name: str, content: bytes) -> tuple[Path, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def test_atomic_commit_recovers_previous_state_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeState(tmp_path)
    state = runtime.load_or_init("fingerprint-a")
    first, first_digest = _artifact(tmp_path, "collect.json", b"immutable collect")
    state = runtime.commit_stage("collect", first, first_digest, None)
    previous_bytes = runtime.state_path.read_bytes()
    second, second_digest = _artifact(tmp_path, "clean.json", b"clean")

    def fail_state_replace(source: Path, target: Path) -> None:
        if target == runtime.state_path:
            raise OSError("simulated state replace failure")
        original_replace(source, target)

    original_replace = os.replace
    with monkeypatch.context() as failure:
        failure.setattr(os, "replace", fail_state_replace)
        with pytest.raises(RuntimeStateError, match="cannot write state") as error:
            runtime.commit_stage("clean", second, second_digest, first_digest)
    assert isinstance(error.value.__cause__, OSError)

    assert runtime.state_path.read_bytes() == previous_bytes
    assert runtime.load_or_init("fingerprint-a") == state
    assert not list(tmp_path.glob(".*.tmp-*"))
    recovered = RuntimeState(tmp_path).commit_stage(
        "clean", second, second_digest, first_digest
    )
    assert recovered["latest_digest"] == second_digest


def test_changed_input_fingerprint_starts_a_fresh_run(tmp_path: Path) -> None:
    runtime = RuntimeState(tmp_path)
    old = runtime.load_or_init("fingerprint-a")
    artifact, digest = _artifact(tmp_path, "collect.json", b"collect")
    runtime.commit_stage("collect", artifact, digest, None)

    fresh = runtime.load_or_init("fingerprint-b")

    assert fresh["version"] == old["version"]
    assert fresh["input_fingerprint"] == "fingerprint-b"
    assert fresh["run_id"] != old["run_id"]
    assert fresh["round"] == 0
    assert fresh["completed_stages"] == {}
    assert fresh["latest_digest"] is None
    assert json.loads(runtime.state_path.read_text())["run_id"] == fresh["run_id"]


@pytest.mark.parametrize("fingerprint", ["fingerprint-a", "fingerprint-b"])
@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_load_rejects_damaged_recorded_artifact(
    tmp_path: Path, fingerprint: str, damage: str
) -> None:
    runtime = RuntimeState(tmp_path)
    state = runtime.load_or_init("fingerprint-a")
    artifact, digest = _artifact(tmp_path, "collect.json", b"collect")
    runtime.commit_stage("collect", artifact, digest, None)
    previous_bytes = runtime.state_path.read_bytes()
    if damage == "missing":
        artifact.unlink()
    else:
        artifact.write_bytes(b"modified after commit")

    expected_error = "missing artifact" if damage == "missing" else "digest mismatch"
    with pytest.raises(RuntimeStateError, match=expected_error):
        runtime.load_or_init(fingerprint)

    assert runtime.state_path.read_bytes() == previous_bytes
    assert state["completed_stages"] == {}


def test_commit_rejects_stale_latest_digest_and_modified_prior_artifact(tmp_path: Path) -> None:
    runtime = RuntimeState(tmp_path)
    runtime.load_or_init("fingerprint-a")
    collect, collect_digest = _artifact(tmp_path, "collect.json", b"collect")
    runtime.commit_stage("collect", collect, collect_digest, None)
    clean, clean_digest = _artifact(tmp_path, "clean.json", b"clean")

    with pytest.raises(RuntimeStateError, match="CAS conflict"):
        runtime.commit_stage("clean", clean, clean_digest, "stale-digest")

    collect.write_bytes(b"modified after commit")
    with pytest.raises(RuntimeStateError, match="digest mismatch"):
        runtime.commit_stage("clean", clean, clean_digest, collect_digest)


def test_concurrent_commits_with_same_expected_digest_allow_exactly_one(tmp_path: Path) -> None:
    RuntimeState(tmp_path).load_or_init("fingerprint-a")
    candidates = [
        _artifact(tmp_path, f"candidate-{index}.json", f"{index}".encode())
        for index in range(12)
    ]
    start = Barrier(len(candidates))

    def commit(index: int) -> str:
        artifact, digest = candidates[index]
        state = RuntimeState(tmp_path)
        start.wait(timeout=10)
        try:
            result = state.commit_stage(
                f"collect:{index}", artifact, digest, expected_latest_digest=None
            )
        except RuntimeStateError as exc:
            assert "CAS conflict" in str(exc)
            return "conflict"
        assert result["latest_digest"] == digest
        return digest

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        outcomes = list(executor.map(commit, range(len(candidates))))

    assert sum(outcome != "conflict" for outcome in outcomes) == 1
    winning_digest = next(outcome for outcome in outcomes if outcome != "conflict")
    persisted = RuntimeState(tmp_path).load_or_init("fingerprint-a")
    assert persisted["latest_digest"] == winning_digest
    assert len(persisted["completed_stages"]) == 1


def test_run_lock_is_nonblocking_and_released_after_context_exit(tmp_path: Path) -> None:
    first = RuntimeState(tmp_path)
    second = RuntimeState(tmp_path)

    with first.run_lock():
        with pytest.raises(RuntimeStateError, match="run lock is already held"):
            with first.run_lock():
                pass
        with pytest.raises(RuntimeStateError, match="run lock is already held"):
            with second.run_lock():
                pass

    with second.run_lock():
        pass

    with pytest.raises(ValueError, match="body failure"):
        with first.run_lock():
            raise ValueError("body failure")

    with second.run_lock():
        pass


@pytest.mark.parametrize("invalid_json", ['{"input_fingerprint":"fingerprint-a"}', '{}', '{'])
def test_changed_fingerprint_still_rejects_invalid_state_schema(
    tmp_path: Path, invalid_json: str
) -> None:
    runtime = RuntimeState(tmp_path)
    runtime.load_or_init("fingerprint-a")
    runtime.state_path.write_text(invalid_json, encoding="utf-8")

    with pytest.raises(RuntimeStateError, match="invalid (schema|state JSON)"):
        runtime.load_or_init("fingerprint-b")
    assert runtime.state_path.read_text(encoding="utf-8") == invalid_json


def test_start_new_forces_a_new_run_and_preserves_old_artifact(tmp_path: Path) -> None:
    runtime = RuntimeState(tmp_path)
    old = runtime.load_or_init("fingerprint-a")
    artifact, digest = _artifact(
        tmp_path, f"artifacts/{old['run_id']}/collect-0.json", b"old envelope"
    )
    runtime.commit_stage("collect:0", artifact, digest, None)

    assert runtime.read_current()["completed_stages"]["collect:0"]["artifact_path"] == (
        f"artifacts/{old['run_id']}/collect-0.json"
    )

    fresh = runtime.start_new("fingerprint-a")

    assert fresh["run_id"] != old["run_id"]
    assert fresh["input_fingerprint"] == "fingerprint-a"
    assert fresh["round"] == 0
    assert fresh["completed_stages"] == {}
    assert fresh["latest_digest"] is None
    assert artifact.exists()
    assert runtime.load_or_init("fingerprint-a") == fresh


def test_concurrent_initialization_preserves_one_run(tmp_path: Path) -> None:
    start = Barrier(12)

    def initialize(index: int) -> str:
        start.wait(timeout=10)
        return RuntimeState(tmp_path).load_or_init("fingerprint-a")["run_id"]

    with ThreadPoolExecutor(max_workers=12) as executor:
        run_ids = list(executor.map(initialize, range(12)))

    assert len(set(run_ids)) == 1
    assert RuntimeState(tmp_path).load_or_init("fingerprint-a")["run_id"] == run_ids[0]


def test_run_lock_excludes_other_process_and_allows_state_transactions(tmp_path: Path) -> None:
    probe = '''
import hashlib
import sys
from pathlib import Path
from research_tool.nine_loop.runtime_state import RuntimeState, RuntimeStateError
runtime = RuntimeState(Path(sys.argv[1]))
try:
    with runtime.run_lock():
        runtime.load_or_init("fingerprint-a")
        artifact = Path(sys.argv[1]) / "collect.json"
        artifact.write_bytes(b"collect")
        runtime.commit_stage("collect", artifact, hashlib.sha256(b"collect").hexdigest(), None)
except RuntimeStateError as exc:
    print(str(exc))
    sys.exit(2)
print("acquired and committed")
'''
    runtime = RuntimeState(tmp_path)
    command = [sys.executable, "-B", "-c", probe, str(tmp_path)]
    with runtime.run_lock():
        blocked = subprocess.run(  # noqa: S603 - fixed test script, current interpreter
            command, capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parents[2],
        )
    assert blocked.returncode == 2, blocked.stderr
    assert "run lock is already held" in blocked.stdout

    acquired = subprocess.run(  # noqa: S603 - fixed test script, current interpreter
        command, capture_output=True, text=True, timeout=10,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert acquired.returncode == 0, acquired.stderr
    assert "acquired and committed" in acquired.stdout
    assert "collect" in runtime.load_or_init("fingerprint-a")["completed_stages"]


def test_windows_lock_branch_imports_without_fcntl_and_excludes_reentry(tmp_path, monkeypatch):
    locked = set()
    calls = []

    def locking(fd, operation, size):
        import msvcrt
        assert size == 1 and os.lseek(fd, 0, os.SEEK_CUR) == 0
        if operation == msvcrt.LK_UNLCK:
            locked.clear()
        elif locked:
            raise OSError(errno.EACCES, "locked")
        else:
            locked.add("byte")
        calls.append(operation)

    fake = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    real_import = builtins.__import__

    def windows_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("fcntl unavailable on Windows")
        if name == "msvcrt":
            return fake
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", windows_import)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "nine_loop/runtime_state.py"))
    runtime = namespace["RuntimeState"](tmp_path)
    with runtime.run_lock():
        with pytest.raises(namespace["RuntimeStateError"], match="already held"):
            with runtime.run_lock():
                pass
    with pytest.raises(ValueError, match="body error"):
        with runtime.run_lock():
            raise ValueError("body error")
    with runtime.run_lock():
        pass
    assert calls == [2, 0, 2, 0, 2, 0]
