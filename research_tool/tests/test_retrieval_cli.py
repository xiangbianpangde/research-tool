from __future__ import annotations

import json

from typer.testing import CliRunner

from research_tool.presentation import cli


runner = CliRunner()


def test_retrieval_cli_renders_query_envelope(monkeypatch, tmp_path):
    calls = []

    class FakeClient:
        def __init__(self, root, *, researchctl_root=None, db_path=None):
            calls.append((root, researchctl_root, db_path))

        def query(self, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "query_type": "current", "results": []}

    monkeypatch.setattr(cli, "ResearchCtlClient", FakeClient, raising=False)
    # The command imports the client lazily from the package; patch that import
    # seam rather than requiring a real ResearchCTL checkout.
    monkeypatch.setattr(
        "research_tool.infrastructure.retrieval.ResearchCtlClient", FakeClient
    )

    result = runner.invoke(
        cli.app,
        [
            "retrieval",
            "query",
            "--root",
            str(tmp_path),
            "--entity",
            "CURRENT",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert calls[1] == {"entity": "CURRENT", "text": None, "semantic": False, "limit": 5}


def test_retrieval_cli_requires_owner_for_sources(tmp_path):
    result = runner.invoke(cli.app, ["retrieval", "sources", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "需要 --owner" in result.output
