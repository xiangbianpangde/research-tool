from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline, create_pipeline
from research_tool.domain.errors import LLMAuthenticationError, LLMError, StageError
from research_tool.domain.models import (
    CleanerConfig,
    CollectionStrategyConfig,
    ExtractorConfig,
    NINE_STAGES,
    PdfIngestConfig,
    PipelineConfig,
)
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.llm.base import LLMClient
from research_tool.tests.test_nine_loop_runner import QUOTE, config as runner_config, model_reply


def _local_input(tmp_path: Path) -> Path:
    source_dir = tmp_path / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "paper.md").write_text(
        "<!-- source: file:///approved/paper.md -->\n"
        "<!-- title: Battery cycle life -->\n\n"
        + QUOTE
        + "\n\n"
        + "The protocol reported methods and limitations in the published study. " * 3,
        encoding="utf-8",
    )
    return source_dir


def _input_config(tmp_path: Path, source_dir: Path | None = None) -> PipelineConfig:
    cfg = runner_config(tmp_path, minimum=1)
    cfg.collector.strategies = CollectionStrategyConfig(enabled=False)
    cfg.cleaner = CleanerConfig(min_content_length=20, dedup_similarity=1.0)
    cfg.input_dir = source_dir or _local_input(tmp_path)
    return cfg


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
    pipe = ResearchPipeline(PipelineConfig(topic="", work_dir=tmp_path))

    with pytest.raises(StageError, match="缺少 topic"):
        await pipe.run()


@pytest.mark.parametrize(
    ("config_update", "message"),
    [
        (
            {"stages": ["collect", "clean", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]},
            "完整九段",
        ),
        (
            {"stages": ["collect", "extract", "clean", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]},
            "完整九段",
        ),
        ({"extractor": ExtractorConfig(enabled=False)}, "九段主链不能关闭 extract"),
    ],
    ids=["partial-chain", "reordered-chain", "disabled-extract"],
)
def test_pipeline_rejects_partial_or_disabled_nine_stage_contract(
    tmp_path, config_update, message
):
    with pytest.raises(ValueError, match=message):
        PipelineConfig(topic="paper", work_dir=tmp_path, **config_update)


def test_disabling_collection_strategies_keeps_the_canonical_stage_chain(tmp_path):
    config = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        collector={"strategies": CollectionStrategyConfig(enabled=False)},
    )

    assert config.stages == list(NINE_STAGES)
    assert config.collector.strategies.enabled is False


@pytest.mark.asyncio
async def test_collect_failure_stops_public_pipeline_before_report(tmp_path):
    empty_input = tmp_path / "empty-input"
    empty_input.mkdir()
    config = _input_config(tmp_path / "output", empty_input)

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.failed_stage == "collect"
    assert result.verdict == "STOP_ERROR"
    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_pipeline_inspection_failure_stops_and_records_failure(tmp_path):
    config = _input_config(tmp_path)
    config.llm_stage_attempts = 1

    class FailingInspectLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "ResearchInspection":
                raise LLMError("provider unavailable")
            return model_reply(prompt, schema)

    pipeline = ResearchPipeline(config, llm=FailingInspectLLM())
    events = [event async for event in pipeline.stream()]

    assert events[-1].stage == "inspect"
    assert events[-1].status == "failed"
    assert pipeline._result.failed_stage == "inspect"
    assert pipeline._result.stage_metrics[-1].stage == "inspect"
    assert pipeline._result.stage_metrics[-1].status == "failed"
    assert pipeline._result.report_result is None
    summary = json.loads(pipeline._result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False
    assert summary["verdict"] == "STOP_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_failure", [True, False])
async def test_inspect_model_authentication_and_provider_failures(tmp_path, auth_failure):
    config = _input_config(tmp_path)
    config.llm_stage_attempts = 1

    class FailingInspectLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "ResearchInspection":
                if auth_failure:
                    raise LLMAuthenticationError("auth failed")
                raise LLMError("provider unavailable")
            return model_reply(prompt, schema)

    pipeline = ResearchPipeline(config, llm=FailingInspectLLM())
    events = [event async for event in pipeline.stream()]

    assert events[-1].stage == "inspect"
    assert events[-1].status == "failed"
    assert pipeline._result.failed_stage == "inspect"
    assert pipeline._result.stage_metrics[-1].stage == "inspect"
    assert pipeline._result.stage_metrics[-1].status == "failed"
    assert pipeline._result.report_result is None


@pytest.mark.asyncio
async def test_completed_and_skipped_stage_metrics_are_recorded(tmp_path):
    config = _input_config(tmp_path)
    first_pipeline = ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    )
    first_events = [event async for event in first_pipeline.stream()]
    first = first_pipeline._result

    assert first.verdict == "STOP_SUCCESS"
    completed = [metric for metric in first.stage_metrics if metric.status == "completed"]
    assert completed
    assert all(metric.stage in NINE_STAGES for metric in completed)
    assert all(isinstance(metric.duration_sec, float) and metric.duration_sec > 0 for metric in completed)
    completed_events = [event for event in first_events if event.status == "completed"]
    assert completed_events
    assert all(event.data["duration_sec"] > 0 for event in completed_events)

    resumed_pipeline = ResearchPipeline(
        config,
        llm=MockLLMClient(structured_response=lambda *args: pytest.fail("resume called model")),
    )
    resumed_events = [event async for event in resumed_pipeline.stream()]
    resumed = resumed_pipeline._result

    skipped = [metric for metric in resumed.stage_metrics if metric.status == "skipped"]
    assert skipped
    assert all(metric.duration_sec == 0 for metric in skipped)
    skipped_events = [
        event
        for event in resumed_events
        if event.status == "skipped" and event.data is not None
    ]
    assert skipped_events
    assert all(event.data["duration_sec"] == 0 for event in skipped_events)
    summary = json.loads(resumed.run_summary_path.read_text(encoding="utf-8"))
    assert [metric["status"] for metric in summary["stage_metrics"]] == [
        metric.status for metric in resumed.stage_metrics
    ]


@pytest.mark.asyncio
async def test_partial_extraction_file_without_committed_stage_is_recomputed(tmp_path, monkeypatch):
    config = _input_config(tmp_path)
    topic_root = config.work_dir / "battery"
    stale = topic_root / "extracted" / "entities.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("[]", encoding="utf-8")
    calls = 0

    async def fail_after_partial(self, input_dir, llm, work_dir=None):
        nonlocal calls
        calls += 1
        partial = Path(work_dir) / "extracted" / "partial.json"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("[]", encoding="utf-8")
        raise StageError("extract", "interrupted after partial output")

    with monkeypatch.context() as patch:
        patch.setattr("research_tool.application.pipeline.Extractor.run", fail_after_partial)
        failed = await ResearchPipeline(
            config, llm=MockLLMClient(structured_response=model_reply)
        ).run()

    assert failed.failed_stage == "extract"
    assert calls == 1
    retry_llm = MockLLMClient(structured_response=model_reply)
    retried = await ResearchPipeline(config, llm=retry_llm).run()

    assert retried.verdict == "STOP_SUCCESS"
    assert any(call.get("schema") and call["schema"].__name__ == "_ChunkResult" for call in retry_llm.calls)
    assert stale.read_text(encoding="utf-8") == "[]"


@pytest.mark.asyncio
async def test_public_input_is_lossless_and_legacy_report_is_not_overwritten(tmp_path):
    source_dir = _local_input(tmp_path)
    source_path = source_dir / "paper.md"
    original_input = source_path.read_bytes()
    config = _input_config(tmp_path / "output", source_dir)
    legacy_report = config.work_dir / "battery" / "report.md"
    legacy_report.parent.mkdir(parents=True)
    legacy_report.write_text("historical report", encoding="utf-8")
    legacy_tree = config.work_dir / "battery" / "tree" / "00.md"
    legacy_tree.parent.mkdir(parents=True)
    legacy_tree.write_text("historical tree", encoding="utf-8")

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert QUOTE in result.report_result.report_path.read_text(encoding="utf-8")
    assert source_path.read_bytes() == original_input
    assert legacy_report.read_text(encoding="utf-8") == "historical report"
    assert legacy_tree.read_text(encoding="utf-8") == "historical tree"
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert result.report_result.report_path == (
        result.topic_dir / "runs" / summary["run_id"] / "final" / "report.md"
    )


@pytest.mark.asyncio
async def test_changed_local_input_starts_new_run_and_preserves_prior_report(tmp_path):
    source_dir = _local_input(tmp_path)
    config = _input_config(tmp_path / "output", source_dir)
    first = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()
    first_report = first.report_result.report_path.read_bytes()
    first_id = json.loads(first.run_summary_path.read_text(encoding="utf-8"))["run_id"]

    (source_dir / "paper.md").write_text(
        "<!-- source: file:///approved/paper.md -->\n"
        "<!-- title: Battery cycle life -->\n\n"
        "A changed experiment confirmed a different result after repeat cycling. "
        "Methods, controls, and limitations were reported with the battery test. " * 2,
        encoding="utf-8",
    )
    changed_quote = "A changed experiment confirmed a different result after repeat cycling."

    def changed_model_reply(prompt, schema):
        if schema.__name__ == "_ChunkResult" and changed_quote in prompt:
            return schema(
                claims=[
                    {"text": changed_quote, "quote": changed_quote, "topic": "Changed result"}
                ]
            )
        return model_reply(prompt, schema)

    second = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=changed_model_reply)
    ).run()
    second_id = json.loads(second.run_summary_path.read_text(encoding="utf-8"))["run_id"]

    assert second_id != first_id
    assert second.report_result.report_path != first.report_result.report_path
    assert first.report_result.report_path.read_bytes() == first_report
    assert len(list((second.topic_dir / "runs").glob("*/final/report.md"))) == 2


@pytest.mark.asyncio
async def test_pdf_translation_uses_real_ingestor_and_mocks_only_ocr(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = pdf_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")
    captured: dict[str, object] = {}

    class FakeOCREngine:
        name = "fixture"

        def available(self):
            return type(
                "Status",
                (),
                {"available": True, "name": self.name, "detail": "fixture OCR"},
            )()

        def parse(self, path: Path, output: Path) -> str:
            captured.update(pdf=path, output=output)
            return "# Paper\n\n" + QUOTE

    monkeypatch.setattr(
        "research_tool.infrastructure.ingest.pdf.create_ocr_engine",
        lambda config: FakeOCREngine(),
    )
    config = runner_config(tmp_path, minimum=1)
    config.pdf_dir = str(pdf_dir)
    config.pdf_ingest = PdfIngestConfig(translate=True, ocr_engine="custom")
    llm = MockLLMClient(
        chat_response=lambda prompt: prompt,
        structured_response=model_reply,
    )

    result = await ResearchPipeline(
        config, llm=llm,  # the real PdfIngestor executes; only the OCR edge is faked
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert captured["pdf"] == pdf
    assert "ocr:fixture + translate" in next((result.topic_dir / "runs").glob("*/initial/raw/*.md")).read_text()
    assert result.report_result.report_path.is_file()
    assert any(call["kind"] == "chat" for call in llm.calls)


@pytest.mark.asyncio
async def test_clean_relevance_filter_runs_in_public_pipeline(tmp_path):
    config = _input_config(tmp_path)
    config.cleaner.relevance_filter = True

    class ScoringLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_Scores":
                return schema(scores=[1.0])
            return model_reply(prompt, schema)

    result = await ResearchPipeline(config, llm=ScoringLLM()).run()

    assert result.verdict == "STOP_SUCCESS"
    clean_file = next((result.topic_dir / "runs").glob("*/initial/clean/*.md"))
    assert QUOTE in clean_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failed_clean_relevance_scoring_stops_before_report(tmp_path):
    config = _input_config(tmp_path)
    config.cleaner.relevance_filter = True

    class FailingScoreLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_Scores":
                raise LLMError("scoring unavailable")
            return model_reply(prompt, schema)

    result = await ResearchPipeline(config, llm=FailingScoreLLM()).run()

    assert result.failed_stage == "clean"
    assert result.report_result is None


@pytest.mark.asyncio
async def test_transient_llm_error_retries_then_completes(tmp_path):
    config = _input_config(tmp_path)
    config.llm_stage_attempts = 2
    config.llm_retry_backoff_sec = 0

    class FlakyExtractLLM(MockLLMClient):
        attempts = 0

        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.attempts += 1
                if self.attempts == 1:
                    raise LLMError("temporary provider timeout")
            return model_reply(prompt, schema)

    llm = FlakyExtractLLM()
    result = await ResearchPipeline(config, llm=llm).run()

    assert result.verdict == "STOP_SUCCESS"
    assert llm.attempts == 2


@pytest.mark.asyncio
async def test_authentication_failure_is_never_retried(tmp_path):
    config = _input_config(tmp_path)
    config.llm_stage_attempts = 3
    config.llm_retry_backoff_sec = 0

    class BadKeyLLM(MockLLMClient):
        attempts = 0

        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.attempts += 1
                raise LLMAuthenticationError("invalid key")
            return model_reply(prompt, schema)

    llm = BadKeyLLM()
    result = await ResearchPipeline(config, llm=llm).run()

    assert result.verdict == "STOP_ERROR"
    assert result.failed_stage == "extract"
    assert llm.attempts == 1


@pytest.mark.asyncio
async def test_private_legacy_dispatch_is_absent(tmp_path):
    pipeline = ResearchPipeline(PipelineConfig(topic="paper", work_dir=tmp_path))

    assert not hasattr(pipeline, "_exec")
    assert not hasattr(pipeline, "_stream_forward")
    assert not hasattr(pipeline, "_stream_backward")
