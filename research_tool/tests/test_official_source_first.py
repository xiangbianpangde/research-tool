from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.domain.errors import CollectError
from research_tool.domain.models import CollectResult, CollectorConfig
from research_tool.infrastructure.search.base import SearchHit, SearchResult
from research_tool.infrastructure.stages.collector import Collector


OFFICIAL = "https://example.com/lab/paper"
RELATED = "https://doi.org/10.1000/related"


def test_official_urls_enable_three_bibliographic_extension_engines():
    cfg = CollectorConfig(official_urls=[OFFICIAL], search_engines=["web"])

    assert cfg.search_engines == ["web", "openalex", "crossref", "arxiv"]


@pytest.mark.asyncio
async def test_official_source_is_fetched_before_bibliographic_search(tmp_path, monkeypatch):
    order: list[str] = []
    cfg = CollectorConfig(
        official_urls=[OFFICIAL],
        search_engines=["openalex", "crossref", "arxiv"],
        search_cache=False,
    )
    collector = Collector(cfg)
    monkeypatch.setattr(
        "research_tool.infrastructure.stages.collector.assert_safe_url",
        lambda _url: None,
    )

    async def fake_fetch(topic: str, hits: list[SearchHit], raw_dir: Path) -> CollectResult:
        order.append("fetch:" + hits[0].source_engine)
        path = raw_dir / f"{len(order):02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("body", encoding="utf-8")
        return CollectResult(files=[path], sources=[], raw_dir=raw_dir, warnings=[])

    async def fake_search(topic: str) -> SearchResult:
        order.append("search")
        return SearchResult(
            hits=[SearchHit(url=RELATED, title="Related", source_engine="openalex")],
            warnings=[],
        )

    monkeypatch.setattr(collector, "fetch_and_store", fake_fetch)
    monkeypatch.setattr(collector, "search_only", fake_search)

    result = await collector.run("paper", tmp_path)

    assert order == ["fetch:official", "search", "fetch:openalex"]
    assert len(result.files) == 2


@pytest.mark.asyncio
async def test_official_fetch_failure_aborts_before_search(tmp_path, monkeypatch):
    cfg = CollectorConfig(official_urls=[OFFICIAL], search_engines=["openalex"])
    collector = Collector(cfg)
    searched = False

    async def fake_fetch(topic: str, hits: list[SearchHit], raw_dir: Path) -> CollectResult:
        return CollectResult(files=[], sources=[], raw_dir=raw_dir, warnings=["fetch failed"])

    async def fake_search(topic: str) -> SearchResult:
        nonlocal searched
        searched = True
        return SearchResult()

    monkeypatch.setattr(collector, "fetch_and_store", fake_fetch)
    monkeypatch.setattr(collector, "search_only", fake_search)

    with pytest.raises(CollectError, match="官方来源"):
        await collector.run("paper", tmp_path)

    assert searched is False


@pytest.mark.asyncio
async def test_official_dry_run_does_not_start_extension_search(tmp_path, monkeypatch):
    collector = Collector(CollectorConfig(official_urls=[OFFICIAL]))
    searched = False

    async def fake_search(topic: str) -> SearchResult:
        nonlocal searched
        searched = True
        return SearchResult()

    monkeypatch.setattr(collector, "search_only", fake_search)

    result = await collector.run("paper", tmp_path, dry_run=True)

    assert searched is False
    assert any("官方来源" in warning for warning in result.warnings)
