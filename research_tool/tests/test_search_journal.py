"""Observed search work is durable across cancellation and new collectors."""
from __future__ import annotations

import asyncio
import json

import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import FetchResult


def config():
    return CollectorConfig(search_engines=["web"], search_cache=False,
                           search_relevance_min_overlap=0, min_doc_chars=1,
                           max_concurrent_searches=1, max_total_results=1,
                           max_results_per_engine=1, deep_search=True, deep_pages=3,
                           deep_sorts=["relevance"])


@pytest.mark.asyncio
async def test_separate_collectors_preserve_search_attempts_without_counting_fetches(tmp_path, monkeypatch):
    class Backend:
        async def search(self, query, *args, **kwargs):
            return [SearchHit(url="https://example.test/a", title="alpha", source_engine="web")]

    async def fetch(self, url):
        return FetchResult(url, "Actual evidence body.", ok=True)

    monkeypatch.setattr("research_tool.infrastructure.stages.collector.get_backend",
                        lambda *_: Backend())
    monkeypatch.setattr("research_tool.infrastructure.stages.collector.Fetcher.fetch", fetch)
    for _ in range(2):
        collector = Collector(config())
        await collector.run("alpha", tmp_path)
    audit = json.loads((tmp_path / "raw/search-audit.json").read_text())
    assert audit["planned_request_count"] == 6
    assert audit["actual_attempt_count"] == 2
    assert audit["successful_hit_count"] == 2
    assert len(audit["calls"]) == 2
    assert len({call["call_id"] for call in audit["calls"]}) == 2


@pytest.mark.asyncio
async def test_cancel_during_search_keeps_plan_and_started_request(tmp_path, monkeypatch):
    entered = asyncio.Event()

    class Backend:
        async def search(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("research_tool.infrastructure.stages.collector.get_backend",
                        lambda *_: Backend())
    task = asyncio.create_task(Collector(config()).run("alpha", tmp_path))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    audit = json.loads((tmp_path / "raw/search-audit.json").read_text())
    assert audit["planned_request_count"] == 3
    assert audit["actual_attempt_count"] == 1
    assert audit["cancelled_request_count"] == 1
    assert audit["failed_request_count"] == 0
    assert audit["successful_hit_count"] == 0
    assert audit["calls"][0]["status"] == "cancelled"
