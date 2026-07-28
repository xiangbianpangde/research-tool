from __future__ import annotations

import json
import subprocess

import pytest

from research_tool.domain.errors import SearchError
from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search import get_backend
from research_tool.infrastructure.search.opencli_backend import OpenCLISearchBackend


def test_opencli_backend_builds_safe_adapter_command():
    backend = OpenCLISearchBackend(
        CollectorConfig(
            search_engines=["opencli"],
            opencli_cmd="/usr/local/bin/opencli",
            opencli_site="google-scholar",
        )
    )

    assert backend._command("graph agents", 7) == [
        "/usr/local/bin/opencli",
        "google-scholar",
        "search",
        "graph agents",
        "--limit",
        "7",
        "-f",
        "json",
    ]


@pytest.mark.asyncio
async def test_opencli_backend_parses_common_json_envelopes(monkeypatch):
    payload = {
        "results": [
            {
                "title": "Paper A",
                "url": "https://example.org/a",
                "snippet": "abstract",
            },
            {"name": "Paper B", "link": "https://example.org/b", "description": "desc"},
            {"title": "missing URL"},
        ]
    }

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = OpenCLISearchBackend(
        CollectorConfig(search_engines=["opencli"], search_cache=False)
    )

    hits = await backend.search("topic", 5)

    assert [hit.url for hit in hits] == ["https://example.org/a", "https://example.org/b"]
    assert hits[0].source_engine == "opencli"
    assert hits[1].title == "Paper B"


@pytest.mark.asyncio
async def test_opencli_backend_surfaces_doctor_or_adapter_failure(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 2, "", "browser bridge offline")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = OpenCLISearchBackend(
        CollectorConfig(search_engines=["opencli"], search_cache=False)
    )

    with pytest.raises(SearchError, match="opencli doctor"):
        await backend.search("topic", 5)


def test_search_factory_registers_opencli_without_cache():
    backend = get_backend(
        "opencli",
        CollectorConfig(search_engines=["opencli"], search_cache=False),
    )

    assert isinstance(backend, OpenCLISearchBackend)
