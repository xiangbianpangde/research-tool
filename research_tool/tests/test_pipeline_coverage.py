from __future__ import annotations

from pathlib import Path
import json

import pytest

from research_tool.application.pipeline import ResearchPipeline, create_pipeline
from research_tool.application.talk_linker import TalkEnrichReport
from research_tool.domain.errors import LLMAuthenticationError, StageError
from research_tool.domain.models import (
    CleanerConfig,
    CleanResult,
    CollectResult,
    DeepenConfig,
    ExtractorConfig,
    OrganizeResult,
    PdfIngestConfig,
    PipelineConfig,
    PipelineResult,
)
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.llm.base import LLMClient


def _organize_result(topic_dir: Path) -> OrganizeResult:
    return OrganizeResult(
        main_table=topic_dir / "tree" / "00.md",
        nodes=[],
        cross_refs={},
        tree_dir=topic_dir / "tree",
    )


def test_pipeline_lazy_llm_factory_and_public_factory(tmp_path, monkeypatch):
    sentinel = MockLLMClient()
    calls: list[object] = []

    def fake_from_config(cls, config):
        calls.append(config)
        return sentinel

    monkeypatch.setattr(LLMClient, "from_config", classmethod(fake_from_config))
    pipe = create_pipeline(PipelineConfig(work_dir=tmp_path))

    assert isinstance(pipe, ResearchPipeline)
    assert pipe._get_llm() is sentinel
    assert pipe._get_llm() is sentinel
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_pipeline_requires_topic(tmp_path):
    pipe = ResearchPipeline(PipelineConfig(topic="", work_dir=tmp_path, stages=[]))

    with pytest.raises(StageError, match="缺少 topic"):
        await pipe.run()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "config_update"),
    [
        ("extract", {"extractor": ExtractorConfig(enabled=False)}),
        ("deepen", {"deepen": DeepenConfig(enabled=False)}),
        ("deepen", {"pdf_dir": "/tmp/papers"}),
    ],
)
async def test_forward_skips_disabled_or_inapplicable_stage(tmp_path, stage, config_update):
    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=[stage],
        resume=False,
        **config_update,
    )

    result = await ResearchPipeline(cfg).run()

    assert result.stages_skipped == [stage]
    assert result.stages_completed == []


@pytest.mark.asyncio
async def test_forward_stage_failure_stops_pipeline(tmp_path, monkeypatch):
    pipe = ResearchPipeline(
        PipelineConfig(
            topic="paper",
            work_dir=tmp_path,
            stages=["collect", "clean"],
            resume=False,
        )
    )

    async def fail_exec(stage, topic, topic_dir, result):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipe, "_exec", fail_exec)

    events = [event async for event in pipe.stream()]

    assert events[-1].status == "failed"
    assert pipe._result is not None
    assert pipe._result.failed_stage == "collect"
    assert pipe._result.stage_metrics[0].stage == "collect"
    assert pipe._result.stage_metrics[0].status == "failed"
    assert pipe._result.stage_metrics[0].duration_sec >= 0
    assert events[-1].data["duration_sec"] >= 0
    assert pipe._result.run_summary_path is not None
    assert pipe._result.run_summary_path.exists()


@pytest.mark.asyncio
async def test_pipeline_records_completed_and_skipped_stage_metrics(tmp_path, monkeypatch):
    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["collect", "clean"],
        resume=True,
    )
    pipe = ResearchPipeline(cfg)
    executed = []

    async def fake_exec(stage, topic, topic_dir, result):
        executed.append(stage)
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "source.md").write_text("source", encoding="utf-8")
            (raw / "source-audit.json").write_text(
                json.dumps({"version": 1, "sources": [{"engine": "web", "retained": 1}]}),
                encoding="utf-8",
            )

    monkeypatch.setattr(pipe, "_exec", fake_exec)
    events = [event async for event in pipe.stream()]

    assert executed == ["collect", "clean"]
    assert [metric.status for metric in pipe._result.stage_metrics] == ["completed", "completed"]
    assert all(metric.duration_sec >= 0 for metric in pipe._result.stage_metrics)
    completed = [event for event in events if event.status == "completed"]
    assert all(event.data["duration_sec"] >= 0 for event in completed)

    second = ResearchPipeline(cfg)
    monkeypatch.setattr(second, "_exec", fake_exec)
    await second.run()

    assert second._result.stage_metrics[0].status == "skipped"
    assert second._result.stage_metrics[0].duration_sec == 0
    assert second._result.run_summary_path.exists()
    summary = json.loads(second._result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["source_audits"] == [{"engine": "web", "retained": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_failure", [True, False])
async def test_backward_assessment_failure_paths(tmp_path, monkeypatch, auth_failure):
    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=[],
        max_backward_rounds=1,
    )
    pipe = ResearchPipeline(cfg)

    async def forward(topic, topic_dir, result):
        result.organize_result = _organize_result(topic_dir)
        if False:
            yield None

    class FailingLLM(MockLLMClient):
        async def healthcheck(self, timeout_sec: float = 10.0) -> None:
            if auth_failure:
                raise LLMAuthenticationError("auth failed")
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(pipe, "_stream_forward", forward)
    monkeypatch.setattr(pipe, "_get_llm", lambda: FailingLLM())

    events = [event async for event in pipe.stream()]

    assert events[-1].stage == "backward"
    assert events[-1].status == "failed"
    assert pipe._result is not None
    assert pipe._result.failed_stage == ("backward" if auth_failure else None)
    assert pipe._result.stage_metrics[-1].stage == "backward"
    assert pipe._result.stage_metrics[-1].status == "failed"


@pytest.mark.asyncio
async def test_talk_enrichment_reports_linker_failure(tmp_path, monkeypatch):
    pipe = ResearchPipeline(PipelineConfig(topic="paper", work_dir=tmp_path))

    async def fail_enrich(self, topic_dir, topic=""):
        raise RuntimeError("youtube unavailable")

    monkeypatch.setattr(
        "research_tool.application.talk_linker.TalkLinker.enrich",
        fail_enrich,
    )

    events = [
        event
        async for event in pipe._run_talk_enrichment(
            "paper", tmp_path, PipelineResult(topic_dir=tmp_path)
        )
    ]

    assert events[-1].status == "failed"
    assert "继续 report" in events[-1].message


@pytest.mark.asyncio
async def test_talk_enrichment_warning_without_files_stops_before_rerun(tmp_path, monkeypatch):
    pipe = ResearchPipeline(PipelineConfig(topic="paper", work_dir=tmp_path))
    pipeline_result = PipelineResult(topic_dir=tmp_path)

    async def fake_enrich(self, topic_dir, topic=""):
        return TalkEnrichReport(warnings=["low confidence"])

    monkeypatch.setattr(
        "research_tool.application.talk_linker.TalkLinker.enrich",
        fake_enrich,
    )

    events = [
        event
        async for event in pipe._run_talk_enrichment(
            "paper", tmp_path, pipeline_result
        )
    ]

    assert events[-1].status == "completed"
    assert "warnings=1" in events[-1].message
    assert pipeline_result.stage_metrics[-1].stage == "talk"
    assert pipeline_result.stage_metrics[-1].status == "completed"


@pytest.mark.asyncio
async def test_talk_enrichment_cleans_outputs_and_skips_disabled_extract(tmp_path, monkeypatch):
    for sub in ("clean", "extracted", "tree"):
        directory = tmp_path / sub
        directory.mkdir()
        (directory / "stale.txt").write_text("stale", encoding="utf-8")
    for name in ("report.md", "report.html"):
        (tmp_path / name).write_text("stale", encoding="utf-8")

    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean", "extract", "organize", "report"],
        extractor=ExtractorConfig(enabled=False),
    )
    pipe = ResearchPipeline(cfg)
    executed: list[str] = []

    async def fake_enrich(self, topic_dir, topic=""):
        return TalkEnrichReport(files_written=[topic_dir / "raw" / "talk.md"])

    async def fake_exec(stage, topic, topic_dir, result):
        executed.append(stage)

    monkeypatch.setattr(
        "research_tool.application.talk_linker.TalkLinker.enrich",
        fake_enrich,
    )
    monkeypatch.setattr(pipe, "_exec", fake_exec)
    monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient())

    events = [
        event
        async for event in pipe._run_talk_enrichment(
            "paper", tmp_path, PipelineResult(topic_dir=tmp_path)
        )
    ]

    assert events[-1].status == "completed"
    assert executed == ["clean", "organize"]
    assert not any((tmp_path / sub).exists() for sub in ("clean", "extracted", "tree"))
    assert not (tmp_path / "report.md").exists()
    assert not (tmp_path / "report.html").exists()


@pytest.mark.asyncio
async def test_exec_pdf_collect_with_translation(tmp_path, monkeypatch):
    collected = CollectResult(files=[], sources=[], raw_dir=tmp_path / "raw")
    captured: dict[str, object] = {}

    class FakePdfIngestor:
        def __init__(self, config, llm):
            captured.update(config=config, llm=llm)

        async def run(self, pdf_dir, topic_dir):
            captured.update(pdf_dir=pdf_dir, topic_dir=topic_dir)
            return collected

    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["collect"],
        pdf_dir=str(tmp_path / "pdfs"),
        pdf_ingest=PdfIngestConfig(translate=True),
    )
    pipe = ResearchPipeline(cfg)
    llm = MockLLMClient()
    monkeypatch.setattr("research_tool.infrastructure.ingest.PdfIngestor", FakePdfIngestor)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)
    result = PipelineResult(topic_dir=tmp_path)

    await pipe._exec("collect", "paper", tmp_path, result)

    assert result.collect_result is collected
    assert captured["llm"] is llm
    assert captured["pdf_dir"] == Path(cfg.pdf_dir)


@pytest.mark.asyncio
async def test_exec_clean_applies_relevance_filter(tmp_path, monkeypatch):
    raw_result = CleanResult(files=[], clean_dir=tmp_path / "clean")
    filtered_result = CleanResult(files=[], clean_dir=tmp_path / "filtered")

    class FakeCleaner:
        def __init__(self, config):
            pass

        def process(self, raw_dir, work_dir):
            return raw_result

        async def filter_relevance(self, result, llm, topic):
            return filtered_result

    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        cleaner=CleanerConfig(relevance_filter=True),
    )
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr("research_tool.application.pipeline.Cleaner", FakeCleaner)
    monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient())
    result = PipelineResult(topic_dir=tmp_path)

    await pipe._exec("clean", "paper", tmp_path, result)

    assert result.clean_result is filtered_result


@pytest.mark.asyncio
async def test_exec_rejects_unknown_stage(tmp_path):
    pipe = ResearchPipeline(PipelineConfig(topic="paper", work_dir=tmp_path))

    with pytest.raises(StageError, match="未知 stage"):
        await pipe._exec("unknown", "paper", tmp_path, PipelineResult(topic_dir=tmp_path))
