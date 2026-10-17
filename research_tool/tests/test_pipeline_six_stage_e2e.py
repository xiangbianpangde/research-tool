"""The former six-stage E2E coverage, expressed through the nine-stage API."""

from __future__ import annotations

import asyncio
import json

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import LLMAuthenticationError, LLMError
from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher
from research_tool.tests.test_nine_loop_runner import QUOTE, SECOND, config, model_reply

OFFICIAL = "https://official.example/paper"
RELATED = "https://doi.org/10.1000/related"
VIDEO_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.mark.asyncio
async def test_official_source_is_fetched_before_search_and_expansion(
    tmp_path, monkeypatch
):
    order: list[str] = []

    class Backend:
        async def search(self, query, *args, **kwargs):
            order.append("search")
            return [
                SearchHit(
                    url=RELATED,
                    title="Related battery study",
                    source_engine="openalex",
                )
            ]

    async def fetch(self, url):
        order.append("fetch:official" if url == OFFICIAL else "fetch:related")
        text = QUOTE if url == OFFICIAL else SECOND
        return FetchResult(
            url=url,
            markdown="# Study\n\n" + text + "\n\n" + "Methods and limitations. " * 8,
            ok=True,
        )

    monkeypatch.setattr("research_tool.infrastructure.stages.collector.get_backend", lambda *a: Backend())
    monkeypatch.setattr(Fetcher, "fetch", fetch)
    monkeypatch.setattr(
        "research_tool.infrastructure.stages.collector.assert_safe_url", lambda url: None
    )

    pipeline_config = config(tmp_path)
    pipeline_config.collector = CollectorConfig(
        official_urls=[OFFICIAL],
        search_engines=["web"],
        max_results_per_engine=1,
        max_total_results=4,
        min_doc_chars=20,
        search_cache=False,
        search_relevance_min_overlap=0,
        save_originals=False,
        strategies={"enabled": False},
    )
    pipeline_config.nine_loop.max_rounds = 0
    llm = MockLLMClient(structured_response=model_reply)

    result = await ResearchPipeline(pipeline_config, llm=llm).run()

    assert result.failed_stage is None
    assert result.verdict == "STOP_SUCCESS"
    assert order[0] == "fetch:official"
    assert order.index("fetch:official") < order.index("search") < order.index("fetch:related")
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    run_root = result.topic_dir / "runs" / summary["run_id"]
    sources = json.loads((run_root / "initial" / "raw" / "sources.json").read_text())
    assert {source["url"] for source in sources} == {OFFICIAL, RELATED}
    official = next(source for source in sources if source["url"] == OFFICIAL)
    assert official["source_engine"] == "official"
    report = result.report_result.report_path.read_text(encoding="utf-8")
    assert QUOTE in report and SECOND in report
    assert OFFICIAL in report and RELATED in report


def _video_config(tmp_path):
    input_dir = tmp_path / "video-input"
    input_dir.mkdir()
    body = f"<!-- source: {VIDEO_URL} -->\n<!-- title: Battery research talk -->\n\n{QUOTE}\n\n" + (
        "Experimental methods, limitations, and reproducibility details. " * 8
    )
    source = input_dir / "battery-talk.md"
    source.write_text(body, encoding="utf-8")
    pipeline_config = config(tmp_path / "out")
    pipeline_config.input_dir = input_dir
    pipeline_config.collector.strategies.enabled = False
    return pipeline_config, source, body


@pytest.mark.asyncio
async def test_local_video_markdown_flows_through_nine_stage_public_run(tmp_path):
    pipeline_config, source, original_markdown = _video_config(tmp_path)
    llm = MockLLMClient(structured_response=model_reply)

    result = await ResearchPipeline(pipeline_config, llm=llm).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.failed_stage is None
    assert result.report_result is not None
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["stages"] == [
        "collect", "clean", "extract", "knowledge", "inspect",
        "targeted", "merge", "qgate", "report",
    ]
    run_root = result.topic_dir / "runs" / summary["run_id"]
    copied = next((run_root / "initial" / "raw").glob("*.md"))
    assert copied.read_text(encoding="utf-8") == original_markdown
    report = result.report_result.report_path.read_text(encoding="utf-8")
    assert QUOTE in report and VIDEO_URL in report
    assert not (result.topic_dir / "report.md").exists()
    assert {"collect", "clean", "extract", "knowledge", "inspect", "qgate", "report"} <= set(
        result.stages_completed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["auth", "model", "cancel"])
async def test_local_video_model_stop_or_failure_never_publishes_report(
    tmp_path, failure
):
    pipeline_config, _source, _body = _video_config(tmp_path)
    pipeline_config.llm_retry_backoff_sec = 0
    started = asyncio.Event()

    if failure == "auth":
        class FailingLLM(MockLLMClient):
            async def chat_structured(self, prompt, schema, system=None):
                if schema.__name__ == "_ChunkResult":
                    raise LLMAuthenticationError("invalid local test credential")
                return model_reply(prompt, schema)

        llm = FailingLLM()
        result = await ResearchPipeline(pipeline_config, llm=llm).run()
        assert result.verdict == "STOP_ERROR"
        assert result.failed_stage == "extract"
    elif failure == "model":
        class FailingLLM(MockLLMClient):
            attempts = 0

            async def chat_structured(self, prompt, schema, system=None):
                if schema.__name__ == "_ChunkResult":
                    self.attempts += 1
                    raise LLMError("offline model failure")
                return model_reply(prompt, schema)

        llm = FailingLLM()
        result = await ResearchPipeline(pipeline_config, llm=llm).run()
        assert llm.attempts == pipeline_config.llm_stage_attempts
        assert result.verdict == "STOP_ERROR"
        assert result.failed_stage == "extract"
    else:
        class WaitingLLM(MockLLMClient):
            async def chat_structured(self, prompt, schema, system=None):
                if schema.__name__ == "_ChunkResult":
                    started.set()
                    await asyncio.Event().wait()
                return model_reply(prompt, schema)

        llm = WaitingLLM()
        pipeline = ResearchPipeline(pipeline_config, llm=llm)
        task = asyncio.create_task(pipeline.run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = pipeline._result
        assert result is not None
        assert result.verdict == "STOP_CANCELLED"
        assert result.failed_stage == "extract"

    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False
