from __future__ import annotations

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from research_tool.infrastructure.retrieval.client import (
    ResearchCtlClient,
    RetrievalClientError,
)


_HELPER = r'''
import json
import os
import sys
import time
from pathlib import Path

log = Path(os.environ["RETRIEVAL_HELPER_LOG"])
with log.open("a", encoding="utf-8") as f:
    f.write(f"start:{sys.argv[-1]}\n")
    f.flush()
mode = os.environ.get("RETRIEVAL_HELPER_MODE", "success")
if mode == "malformed":
    print("not-json")
    raise SystemExit(0)
if mode == "nonzero":
    print(json.dumps({"status": "success", "query_type": sys.argv[-1]}))
    raise SystemExit(3)
if mode == "timeout":
    time.sleep(float(os.environ.get("RETRIEVAL_HELPER_SLEEP", "1")))
if mode == "slow":
    time.sleep(float(os.environ.get("RETRIEVAL_HELPER_SLEEP", "0.05")))
print(json.dumps({"status": "success", "query_type": sys.argv[-1]}))
'''


def _client(tmp_path: Path, monkeypatch, *, mode: str = "success", timeout: float = 1.0):
    helper = tmp_path / "researchctl_helper.py"
    helper.write_text(_HELPER, encoding="utf-8")
    log = tmp_path / "helper.log"
    monkeypatch.setenv("RETRIEVAL_HELPER_LOG", str(log))
    monkeypatch.setenv("RETRIEVAL_HELPER_MODE", mode)
    return ResearchCtlClient(
        tmp_path / "output",
        researchctl_root=tmp_path,
        python_executable=sys.executable,
        timeout_sec=timeout,
    ), helper, log


def test_client_builds_research_tool_command_and_parses_envelope(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"status":"success","results":[]}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ResearchCtlClient(
        tmp_path / "output",
        researchctl_root=tmp_path / "researchctl",
        db_path=tmp_path / "index.sqlite",
    )

    payload = client.query(entity="CURRENT", limit=3)

    assert payload["status"] == "success"
    command, kwargs = calls[0]
    assert command[:5] == [client.python_executable, "-m", "researchctl", "--layout", "research-tool"]
    assert "--root" in command and str((tmp_path / "output").resolve()) in command
    assert "--db" in command and str((tmp_path / "index.sqlite").resolve()) in command
    assert command[-5:] == ["query", "--entity", "CURRENT", "--limit", "3"]
    assert str((tmp_path / "researchctl").resolve()) in kwargs["env"]["PYTHONPATH"]
    assert kwargs["cwd"] == (tmp_path / "researchctl").resolve()


def test_client_does_not_infer_module_root_from_topic_root(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, '{"status":"success"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ResearchCtlClient(tmp_path / "topic")
    client.reconcile()
    assert calls[0]["cwd"] is None
    assert str(tmp_path / "topic") not in calls[0]["env"].get("PYTHONPATH", "")


def test_client_rejects_invalid_limit_and_empty_owner(tmp_path):
    client = ResearchCtlClient(tmp_path)

    with pytest.raises(ValueError, match="limit"):
        client.query(limit=0)
    with pytest.raises(ValueError, match="owner"):
        client.sources(" ")


def test_client_fail_closed_on_invalid_child_output(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "not-json", "secret stderr must not escape")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ResearchCtlClient(tmp_path)

    with pytest.raises(RetrievalClientError, match="invalid JSON") as error:
        client.reconcile()
    assert error.value.envelope == {
        "status": "error",
        "action": "reconcile",
        "error_code": "invalid_json",
        "returncode": 1,
    }
    assert "secret" not in str(error.value)


def test_client_preserves_nonzero_json_envelope_and_does_not_claim_success(tmp_path, monkeypatch):
    client, helper, _ = _client(tmp_path, monkeypatch, mode="nonzero")
    monkeypatch.setattr(client, "_command", lambda action, args: [sys.executable, str(helper), action])

    payload = client.reconcile()

    assert payload["status"] == "error"
    assert payload["error_code"] == "nonzero_exit"
    assert payload["returncode"] == 3


def test_client_timeout_is_safe_and_controlled(tmp_path, monkeypatch):
    client, helper, _ = _client(tmp_path, monkeypatch, mode="timeout", timeout=0.05)
    monkeypatch.setattr(client, "_command", lambda action, args: [sys.executable, str(helper), action])

    with pytest.raises(RetrievalClientError, match="timed out") as error:
        client.reconcile()
    assert error.value.envelope == {
        "status": "error",
        "action": "reconcile",
        "error_code": "timeout",
    }


def test_client_rejects_null_bytes_before_process_launch(monkeypatch, tmp_path):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not be started")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ResearchCtlClient(tmp_path)

    with pytest.raises(ValueError, match="safe strings"):
        client.execute("query", "--text", "bad\x00value")
    assert called is False


def test_client_refresh_is_singleflight_for_same_topic(tmp_path, monkeypatch):
    client, helper, log = _client(tmp_path, monkeypatch, mode="slow")
    monkeypatch.setattr(client, "_command", lambda action, args: [sys.executable, str(helper), action])
    barrier = threading.Barrier(2)

    def refresh():
        barrier.wait(timeout=2)
        return client.refresh()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: refresh(), range(2)))

    assert all(result["index"]["status"] == "success" for result in results)
    actions = [line.strip() for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(actions) == 4
    assert actions == ["start:index", "start:generate-index", "start:index", "start:generate-index"]


def test_refresh_state_path_is_derived_and_stale_is_recoverable(tmp_path, monkeypatch):
    client, helper, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "_command", lambda action, args: [sys.executable, str(helper), action])
    result = client.refresh()
    assert result["navigation"]["query_type"] == "generate-index"
    lock = tmp_path / "output" / ".index" / "retrieval-refresh" / "output.lock"
    assert lock.is_file()
