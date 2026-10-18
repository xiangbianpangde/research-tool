from __future__ import annotations

import subprocess

import pytest

from research_tool.infrastructure.retrieval.client import (
    ResearchCtlClient,
    RetrievalClientError,
)


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


def test_client_rejects_invalid_limit_and_empty_owner(tmp_path):
    client = ResearchCtlClient(tmp_path)

    with pytest.raises(ValueError, match="limit"):
        client.query(limit=0)
    with pytest.raises(ValueError, match="owner"):
        client.sources(" ")


def test_client_fail_closed_on_invalid_child_output(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "not-json", "child failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ResearchCtlClient(tmp_path)

    with pytest.raises(RetrievalClientError, match="invalid JSON"):
        client.reconcile()


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
