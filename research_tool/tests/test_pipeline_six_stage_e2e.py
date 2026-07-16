from __future__ import annotations

import json

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import LLMAuthenticationError
from research_tool.domain.models import (
    CleanerConfig,
    CollectorConfig,
    DeepenConfig,
    OrganizerConfig,
    PipelineConfig,
    PipelineResult,
    TalkConfig,
)
from research_tool.application.talk_linker import TalkEnrichReport
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.search.base import SearchHit, SearchResult
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher

OFFICIAL = "https://official.example/paper"
RELATED = "https://doi.org/10.1000/related"


def _structured(prompt, schema):
    name = schema.__name__
    if name == "_Queries":
        return schema(queries=[])
    if name == "_ChunkResult":
        return schema(
            entities=[{"name": "OfficialPaper", "type": "Paper", "confidence": 1.0}],
            triples=[{"head": "OfficialPaper", "relation": "studies", "tail": "Workspace"}],
        )
    if name == "_Points":
        return schema(points=["官方论文提出并验证了一个可复现的方法。"])
    if name == "_NodePlan":
        return schema(
            nodes=[
                {"title": "方法", "core_question": "方法是什么？"},
                {"title": "证据", "core_question": "证据是什么？"},
            ],
            main_thread="方法 → 证据",
        )
    return schema()


@pytest.mark.asyncio
async def test_complete_six_stage_pipeline_with_official_source_first(tmp_path, monkeypatch):
    search_calls: list[str] = []

    async def fake_search(self, queries, **kwargs):
        search_calls.append("search")
        return SearchResult(
            hits=[SearchHit(url=RELATED, title="Related paper", source_engine="openalex")],
            warnings=[],
        )

    async def fake_fetch(self, url):
        body = (
            "# Official research paper\n\n"
            "This official paper describes its method, experiments, evidence, limitations, "
            "and reproducibility details in enough depth for the complete research pipeline. "
        ) * 5
        return FetchResult(url=url, markdown=body, ok=True)

    monkeypatch.setattr(Collector, "search_queries", fake_search)
    monkeypatch.setattr(Fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(
        "research_tool.infrastructure.stages.collector.assert_safe_url", lambda url: None
    )

    cfg = PipelineConfig(
        topic="official paper",
        work_dir=tmp_path / "out",
        stages=["collect", "deepen", "clean", "extract", "organize", "report"],
        resume=False,
        collector=CollectorConfig(
            official_urls=[OFFICIAL],
            search_engines=["openalex", "crossref", "arxiv"],
            max_results_per_engine=1,
            max_total_results=4,
            min_doc_chars=20,
            search_cache=False,
        ),
        deepen=DeepenConfig(
            entity_split=False,
            breadth=2,
            depth=1,
            profile_extract=False,
            gap_detection=False,
            contradiction_check=False,
        ),
        cleaner=CleanerConfig(min_content_length=20),
        organizer=OrganizerConfig(min_nodes=2, max_nodes=2),
    )
    llm = MockLLMClient(chat_response="（mock 生成的完整正文）", structured_response=_structured)
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)

    result = await pipe.run("official paper")

    assert result.failed_stage is None
    assert result.stages_completed == [
        "collect",
        "deepen",
        "clean",
        "extract",
        "organize",
        "report",
    ]
    topic_dir = result.topic_dir
    assert (topic_dir / "raw" / ".deepen_done").exists()
    assert list((topic_dir / "clean").glob("*.md"))
    assert (topic_dir / "extracted" / "entities.json").exists()
    assert (topic_dir / "tree" / "00-主表.md").exists()
    assert (topic_dir / "report.md").stat().st_size > 0
    sources = json.loads((topic_dir / "raw" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["url"] == OFFICIAL
    assert sources[0]["source_engine"] == "official"
    assert search_calls
    assert [call["kind"] for call in llm.calls].count("health") == 4


@pytest.mark.asyncio
async def test_talk_rerun_healthchecks_each_llm_stage(tmp_path, monkeypatch):
    order: list[str] = []
    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean", "extract", "organize", "report"],
        resume=False,
        cleaner=CleanerConfig(relevance_filter=True),
        talk=TalkConfig(enabled=True),
    )
    pipe = ResearchPipeline(cfg)
    llm = MockLLMClient()

    async def healthcheck(timeout_sec: float = 10.0) -> None:
        order.append("health")

    async def fake_exec(stage, topic, topic_dir, result):
        order.append(stage)

    async def fake_enrich(self, topic_dir, topic=""):
        return TalkEnrichReport(files_written=[topic_dir / "raw" / "talk.md"])

    monkeypatch.setattr(llm, "healthcheck", healthcheck)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)
    monkeypatch.setattr(pipe, "_exec", fake_exec)
    monkeypatch.setattr(
        "research_tool.application.talk_linker.TalkLinker.enrich",
        fake_enrich,
    )

    events = [
        event
        async for event in pipe._run_talk_enrichment(
            "paper",
            tmp_path,
            PipelineResult(topic_dir=tmp_path),
        )
    ]

    assert not [event for event in events if event.status == "failed"]
    assert order == ["health", "clean", "health", "extract", "health", "organize"]


@pytest.mark.asyncio
async def test_talk_rerun_failure_stops_outer_forward_before_report(tmp_path, monkeypatch):
    order: list[str] = []
    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["organize", "report"],
        resume=False,
        talk=TalkConfig(enabled=True),
    )
    pipe = ResearchPipeline(cfg)
    llm = MockLLMClient()
    health_calls = 0

    async def healthcheck(timeout_sec: float = 10.0) -> None:
        nonlocal health_calls
        health_calls += 1
        order.append("health")
        if health_calls == 2:
            raise LLMAuthenticationError("LLM 鉴权失败")

    async def fake_exec(stage, topic, topic_dir, result):
        order.append(stage)

    async def fake_enrich(self, topic_dir, topic=""):
        return TalkEnrichReport(files_written=[topic_dir / "raw" / "talk.md"])

    monkeypatch.setattr(llm, "healthcheck", healthcheck)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)
    monkeypatch.setattr(pipe, "_exec", fake_exec)
    monkeypatch.setattr(
        "research_tool.application.talk_linker.TalkLinker.enrich",
        fake_enrich,
    )

    result = await pipe.run("paper")

    assert result.failed_stage == "organize"
    assert order == ["health", "organize", "health"]
    assert "report" not in result.stages_completed
