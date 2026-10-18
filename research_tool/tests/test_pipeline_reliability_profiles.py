from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError, LLMAuthenticationError, LLMError, StageError
from research_tool.domain.models import ExtractorConfig, LLMConfig, NINE_STAGES, PipelineConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.stages.extractor import Extractor
from research_tool.tests.test_nine_loop_runner import QUOTE, config as runner_config, model_reply


@pytest.fixture(autouse=True)
def _isolate_local_config(monkeypatch):
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)
    monkeypatch.setattr("research_tool.domain.config._DEFAULT_PATHS", [])


def _input_dir(tmp_path: Path) -> Path:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir(parents=True)
    (input_dir / "paper.md").write_text(
        "<!-- source: file:///approved/paper.md -->\n"
        "<!-- title: Battery cycle life -->\n\n"
        + QUOTE
        + "\n\n"
        + "The protocol reported methods, controls, and limitations. " * 4,
        encoding="utf-8",
    )
    return input_dir


def _pipeline_config(tmp_path: Path, input_dir: Path | None = None) -> PipelineConfig:
    config = runner_config(tmp_path, minimum=1)
    config.collector.strategies.enabled = False
    config.input_dir = input_dir or _input_dir(tmp_path)
    config.llm_retry_backoff_sec = 0
    return config


@pytest.mark.parametrize(
    ("mode", "attempts"),
    [("full", 3), ("standard", 2), ("deep", 2)],
)
def test_supported_modes_keep_the_same_nine_stage_contract(tmp_path, monkeypatch, mode, attempts):
    monkeypatch.chdir(tmp_path)

    config = load_config(overrides={"mode": mode})

    assert config.mode == mode
    assert config.stages == list(NINE_STAGES)
    assert config.llm_stage_attempts == attempts
    assert config.extractor.enabled is True
    assert config.extractor.fail_on_chunk_error is True


def test_brief_mode_is_explicitly_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigValidationError, match="brief.*已移除"):
        load_config(overrides={"mode": "brief"})


@pytest.mark.asyncio
async def test_partial_extraction_output_without_committed_stage_is_recomputed(tmp_path, monkeypatch):
    config = _pipeline_config(tmp_path)
    partial_files: list[Path] = []

    async def interrupt_after_partial(self, input_dir, llm, work_dir=None):
        partial = Path(work_dir) / "extracted" / "partial.json"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("[]", encoding="utf-8")
        partial_files.append(partial)
        raise StageError("extract", "interrupted after partial extraction output")

    with monkeypatch.context() as patch:
        patch.setattr(Extractor, "run", interrupt_after_partial)
        failed = await ResearchPipeline(
            config, llm=MockLLMClient(structured_response=model_reply)
        ).run()

    assert failed.failed_stage == "extract"
    assert partial_files and partial_files[0].is_file()
    assert json.loads(failed.run_summary_path.read_text(encoding="utf-8"))["pipeline_complete"] is False

    llm = MockLLMClient(structured_response=model_reply)
    retried = await ResearchPipeline(config, llm=llm).run()

    assert retried.verdict == "STOP_SUCCESS"
    assert any(
        call.get("schema") is not None and call["schema"].__name__ == "_ChunkResult"
        for call in llm.calls
    )
    assert partial_files[0].is_file()


@pytest.mark.asyncio
async def test_completed_report_is_reused_only_while_committed_output_is_intact(tmp_path):
    config = _pipeline_config(tmp_path)
    first = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()
    report_path = first.report_result.report_path
    assert report_path.is_file()

    report_path.unlink()
    resumed = await ResearchPipeline(
        config,
        llm=MockLLMClient(structured_response=lambda *args: pytest.fail("invalid cache called model")),
    ).run()

    assert resumed.verdict == "STOP_ERROR"
    assert resumed.report_result is None
    summary = json.loads(resumed.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_transient_llm_error_retries_then_completes(tmp_path):
    config = _pipeline_config(tmp_path)

    class FlakyLLM(MockLLMClient):
        attempts = 0

        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.attempts += 1
                if self.attempts == 1:
                    raise LLMError("temporary provider timeout")
            return model_reply(prompt, schema)

    llm = FlakyLLM()
    result = await ResearchPipeline(config, llm=llm).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.failed_stage is None
    assert llm.attempts == 2


@pytest.mark.asyncio
async def test_authentication_failure_is_never_retried(tmp_path):
    config = _pipeline_config(tmp_path)

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
    assert result.report_result is None
    assert llm.attempts == 1


@pytest.mark.parametrize("mode", ["full", "standard", "deep"])
@pytest.mark.asyncio
async def test_report_uses_cleaned_source_evidence_and_versioned_final_path(tmp_path, mode):
    config = _pipeline_config(tmp_path / mode)
    config.mode = mode
    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.report_result is not None
    report = result.report_result.report_path.read_text(encoding="utf-8")
    assert QUOTE in report
    clean_file = next((result.topic_dir / "runs").glob("*/initial/clean/*.md"))
    assert QUOTE in clean_file.read_text(encoding="utf-8")
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert result.report_result.report_path == (
        result.topic_dir / "runs" / summary["run_id"] / "final" / "report.md"
    )
    assert summary["pipeline_complete"] is True


@pytest.mark.asyncio
async def test_pipeline_retries_transient_structured_extraction_failure(tmp_path):
    config = _pipeline_config(tmp_path)
    config.llm_stage_attempts = 2
    calls = 0

    class FlakyExtractLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            nonlocal calls
            if schema.__name__ == "_ChunkResult":
                calls += 1
                if calls == 1:
                    raise LLMError("malformed structured response") from ValueError("invalid JSON")
            return model_reply(prompt, schema)

    result = await ResearchPipeline(config, llm=FlakyExtractLLM()).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.report_result is not None
    assert calls == 2


@pytest.mark.asyncio
async def test_extractor_retries_transient_structured_json_failure(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "01.md").write_text(
        "<!-- source: file:///approved/evidence.md -->\n\n"
        + QUOTE,
        encoding="utf-8",
    )

    class FlakyLLM(MockLLMClient):
        chunk_calls = 0

        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.chunk_calls += 1
                if self.chunk_calls == 1:
                    raise LLMError("malformed structured response") from json.JSONDecodeError(
                        "invalid JSON", "", 0
                    )
            return model_reply(prompt, schema)

    llm = FlakyLLM(config=LLMConfig(
        provider="openai_compatible",
        model="fixture-model",
        base_url="https://fixture.invalid/v1",
        api_key="fixture-key",
    ))
    result = await Extractor(ExtractorConfig(fail_on_chunk_error=True)).run(clean, llm)

    assert result.claims
    assert llm.chunk_calls == 2


@pytest.mark.asyncio
async def test_extractor_retries_empty_structured_result(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "01.md").write_text(
        "<!-- source: file:///approved/evidence.md -->\n\n"
        + QUOTE,
        encoding="utf-8",
    )

    class EmptyOnceLLM(MockLLMClient):
        chunk_calls = 0

        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.chunk_calls += 1
                if self.chunk_calls == 1:
                    return schema()
            return model_reply(prompt, schema)

    llm = EmptyOnceLLM(config=LLMConfig(
        provider="openai_compatible",
        model="fixture-model",
        base_url="https://fixture.invalid/v1",
        api_key="fixture-key",
    ))
    result = await Extractor(ExtractorConfig(fail_on_chunk_error=True)).run(clean, llm)

    assert result.claims
    assert llm.chunk_calls == 2


@pytest.mark.asyncio
async def test_full_extractor_aggregates_llm_chunk_failures_and_preserves_cause(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "01.md").write_text(
        "<!-- source: file:///approved/evidence.md -->\n\n"
        "The experiment retained 90 percent capacity after repeated cycles.",
        encoding="utf-8",
    )

    class FailingLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            raise LLMError("provider timeout")

    with pytest.raises(StageError, match="1/1") as captured:
        await Extractor(
            ExtractorConfig(fail_on_chunk_error=True)
        ).run(clean, FailingLLM())

    assert isinstance(captured.value.__cause__, LLMError)


@pytest.mark.asyncio
async def test_pipeline_never_publishes_after_all_extraction_chunks_fail(tmp_path):
    config = _pipeline_config(tmp_path)
    config.llm_stage_attempts = 1

    class FailingExtractLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                raise LLMError("provider timeout")
            return model_reply(prompt, schema)

    result = await ResearchPipeline(config, llm=FailingExtractLLM()).run()

    assert result.failed_stage == "extract"
    assert result.verdict == "STOP_ERROR"
    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    assert json.loads(result.run_summary_path.read_text(encoding="utf-8"))["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_non_llm_extraction_stage_error_is_not_retried(tmp_path, monkeypatch):
    config = _pipeline_config(tmp_path)
    config.llm_stage_attempts = 3
    calls = 0

    async def invalid_evidence(self, input_dir, llm, work_dir=None):
        nonlocal calls
        calls += 1
        raise StageError("extract", "evidence validation failed")

    monkeypatch.setattr(Extractor, "run", invalid_evidence)
    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.failed_stage == "extract"
    assert result.report_result is None
    assert calls == 1
