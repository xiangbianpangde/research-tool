from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.config import load_config
from research_tool.domain.errors import LLMAuthenticationError, StageError
from research_tool.domain.models import ExtractorConfig, PipelineConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.stages.extractor import Extractor


@pytest.mark.parametrize(
    ("mode", "stages"),
    [
        ("brief", ["collect", "clean", "report"]),
        ("full", ["collect", "deepen", "clean", "extract", "organize", "report"]),
    ],
)
def test_brief_and_full_modes_have_explicit_output_contracts(tmp_path, monkeypatch, mode, stages):
    monkeypatch.chdir(tmp_path)

    config = load_config(overrides={"mode": mode})

    assert config.mode == mode
    assert config.stages == stages
    assert config.llm_stage_attempts == (2 if mode == "brief" else 3)


@pytest.mark.asyncio
async def test_partial_llm_output_without_completion_marker_is_not_resumed_as_complete(
    tmp_path,
    monkeypatch,
):
    topic_dir = tmp_path / "paper"
    extracted = topic_dir / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "entities.json").write_text("[]", encoding="utf-8")
    pipeline = ResearchPipeline(
        PipelineConfig(topic="paper", work_dir=tmp_path, stages=["extract"], resume=True)
    )
    pipeline._llm = MockLLMClient()
    executed: list[str] = []

    async def fake_exec(stage, topic, current_topic_dir, result):
        executed.append(stage)

    monkeypatch.setattr(pipeline, "_exec", fake_exec)

    result = await pipeline.run()

    assert executed == ["extract"]
    assert result.stages_completed == ["extract"]
    assert pipeline._completion_marker(topic_dir, "extract").is_file()


@pytest.mark.asyncio
async def test_completed_llm_stage_is_skipped_only_after_atomic_marker(tmp_path, monkeypatch):
    config = PipelineConfig(topic="paper", work_dir=tmp_path, stages=["report"], resume=True)
    first = ResearchPipeline(config)
    first._llm = MockLLMClient()

    async def fake_exec(stage, topic, topic_dir, result):
        (topic_dir / "report.md").write_text("done", encoding="utf-8")

    monkeypatch.setattr(first, "_exec", fake_exec)
    await first.run()

    second = ResearchPipeline(config)
    second._llm = MockLLMClient()
    calls: list[str] = []

    async def should_not_run(stage, topic, topic_dir, result):
        calls.append(stage)

    monkeypatch.setattr(second, "_exec", should_not_run)
    result = await second.run()

    assert calls == []
    assert result.stages_skipped == ["report"]


@pytest.mark.asyncio
async def test_transient_llm_stage_failure_retries_then_completes(tmp_path, monkeypatch):
    pipeline = ResearchPipeline(
        PipelineConfig(
            topic="paper",
            work_dir=tmp_path,
            stages=["report"],
            resume=False,
            llm_stage_attempts=2,
            llm_retry_backoff_sec=0,
        )
    )
    pipeline._llm = MockLLMClient()
    calls = 0

    async def flaky_exec(stage, topic, topic_dir, result):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider timeout")
        (topic_dir / "report.md").write_text("done", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_exec", flaky_exec)

    result = await pipeline.run()

    assert calls == 2
    assert result.failed_stage is None
    assert result.stages_completed == ["report"]


@pytest.mark.asyncio
async def test_authentication_failure_is_never_retried(tmp_path, monkeypatch):
    pipeline = ResearchPipeline(
        PipelineConfig(
            topic="paper",
            work_dir=tmp_path,
            stages=["report"],
            resume=False,
            llm_stage_attempts=3,
            llm_retry_backoff_sec=0,
        )
    )
    pipeline._llm = MockLLMClient()
    calls = 0

    async def auth_fail(stage, topic, topic_dir, result):
        nonlocal calls
        calls += 1
        raise LLMAuthenticationError("bad key")

    monkeypatch.setattr(pipeline, "_exec", auth_fail)

    result = await pipeline.run()

    assert calls == 1
    assert result.failed_stage == "report"


@pytest.mark.asyncio
async def test_brief_report_reads_clean_material_instead_of_missing_tree(tmp_path, monkeypatch):
    pipeline = ResearchPipeline(
        PipelineConfig(topic="paper", mode="brief", work_dir=tmp_path, stages=["report"])
    )
    pipeline._llm = MockLLMClient()
    seen: list[Path] = []

    async def fake_report(self, input_dir, llm, topic="", output_path=None):
        seen.append(input_dir)
        return None

    monkeypatch.setattr("research_tool.application.pipeline.Reporter.run", fake_report)

    await pipeline.run()

    assert seen == [tmp_path / "paper" / "clean"]


@pytest.mark.asyncio
async def test_full_extractor_does_not_mark_all_failed_chunks_as_success(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "01.md").write_text("evidence", encoding="utf-8")

    class FailingLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            raise RuntimeError("provider timeout")

    with pytest.raises(StageError, match="1/1"):
        await Extractor(ExtractorConfig(fail_on_chunk_error=True)).run(clean, FailingLLM())
