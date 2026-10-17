"""Backward-loop behavior through the public nine-stage pipeline contract.

The historical tests in this module exercised the removed private backward
dispatcher. These cases keep the useful behavior at the current boundary:
versioned outputs preserve old work, a CONTINUE round collects a new body and
re-inspects it, and every stop or failure remains fail-closed.
"""

from __future__ import annotations

import json

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher
from research_tool.tests.test_nine_loop_runner import (
    QUOTE,
    SECOND,
    model_reply,
    config as runner_config,
)

pytest_plugins = ["research_tool.tests.test_nine_loop_runner"]


def _config(tmp_path, *, minimum: int = 1, max_rounds: int = 2):
    config = runner_config(tmp_path, minimum=minimum)
    config.collector.strategies.enabled = False
    config.nine_loop.max_rounds = max_rounds
    config.llm_retry_backoff_sec = 0
    return config


@pytest.mark.asyncio
async def test_legacy_raw_report_and_tree_are_preserved_by_versioned_run(
    tmp_path, external_io
):
    config = _config(tmp_path)
    topic_dir = tmp_path / "battery"
    legacy_raw = topic_dir / "raw" / "old-source.md"
    legacy_raw.parent.mkdir(parents=True)
    legacy_raw.write_text("old raw body", encoding="utf-8")
    legacy_report = topic_dir / "report.md"
    legacy_report.write_text("old report", encoding="utf-8")
    legacy_tree = topic_dir / "tree" / "00-主表.md"
    legacy_tree.parent.mkdir()
    legacy_tree.write_text("old tree", encoding="utf-8")

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert legacy_raw.read_text(encoding="utf-8") == "old raw body"
    assert legacy_report.read_text(encoding="utf-8") == "old report"
    assert legacy_tree.read_text(encoding="utf-8") == "old tree"
    assert result.report_result is not None
    assert result.report_result.report_path != legacy_report
    assert result.report_result.report_path.is_relative_to(topic_dir / "runs")


@pytest.mark.asyncio
async def test_missing_initial_downstream_directories_are_safe(tmp_path, external_io):
    config = _config(tmp_path)

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.failed_stage is None
    assert result.report_result is not None
    assert result.report_result.report_path.is_file()
    for directory in ("clean", "extracted", "tree"):
        assert not (result.topic_dir / directory).exists()


@pytest.mark.asyncio
async def test_targeted_round_appends_new_body_without_replacing_initial_sources(
    tmp_path, external_io
):
    config = _config(tmp_path, minimum=2)

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.rounds == 1
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    run_root = result.topic_dir / "runs" / summary["run_id"]
    initial_files = sorted((run_root / "initial" / "raw").glob("*.md"))
    targeted_files = sorted((run_root / "targeted-0" / "raw").glob("*.md"))
    assert initial_files and targeted_files
    assert {path.name for path in initial_files}.isdisjoint(
        path.name for path in targeted_files
    )
    initial_text = "\n".join(path.read_text(encoding="utf-8") for path in initial_files)
    targeted_text = "\n".join(path.read_text(encoding="utf-8") for path in targeted_files)
    assert QUOTE in initial_text
    assert SECOND in targeted_text
    report = result.report_result.report_path.read_text(encoding="utf-8")
    assert QUOTE in report
    assert SECOND in report


@pytest.mark.asyncio
async def test_continue_reinspects_once_then_publishes_after_stop(tmp_path, external_io):
    config = _config(tmp_path, minimum=2, max_rounds=1)
    events = []
    pipeline = ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    )

    async for event in pipeline.stream():
        events.append(event)

    result = pipeline._result
    assert result is not None
    assert result.verdict == "STOP_SUCCESS"
    assert result.rounds == 1
    requests = [
        event.data["request_id"]
        for event in events
        if event.status == "completed" and event.data
    ]
    assert requests.index("inspect:0") < requests.index("qgate:0")
    assert requests.index("qgate:0") < requests.index("targeted:0")
    assert requests.index("targeted:0") < requests.index("merge:0")
    assert requests.index("merge:0") < requests.index("inspect:1")
    assert requests.index("inspect:1") < requests.index("qgate:1")
    assert requests.index("qgate:1") < requests.index("report")


@pytest.mark.asyncio
async def test_no_gap_skips_targeted_and_merge(tmp_path, external_io):
    config = _config(tmp_path, minimum=1)

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert "targeted" in result.stages_skipped
    assert "merge" in result.stages_skipped
    assert "targeted" not in result.stages_completed
    assert "merge" not in result.stages_completed
    assert not list(result.topic_dir.glob("runs/*/targeted-*"))


@pytest.mark.asyncio
async def test_unverified_fact_fails_closed_before_report(tmp_path, external_io):
    def hallucinated_fact(prompt, schema):
        if schema.__name__ == "_ChunkResult":
            return schema(
                claims=[
                    {
                        "text": "invented claim",
                        "quote": "absent from source",
                        "topic": "Cycle life",
                    }
                ]
            )
        return model_reply(prompt, schema)

    result = await ResearchPipeline(
        _config(tmp_path), llm=MockLLMClient(structured_response=hallucinated_fact)
    ).run()

    assert result.failed_stage == "extract"
    assert result.verdict == "STOP_ERROR"
    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_front_stage_error_does_not_publish_report(tmp_path, external_io, monkeypatch):
    async def failed_fetch(self, url):
        return FetchResult(url=url, markdown="", ok=False)

    monkeypatch.setattr(Fetcher, "fetch", failed_fetch)
    config = _config(tmp_path)
    legacy_report = tmp_path / "battery" / "report.md"
    legacy_report.parent.mkdir(parents=True)
    legacy_report.write_text("keep this old report", encoding="utf-8")

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.failed_stage == "collect"
    assert result.verdict == "STOP_ERROR"
    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    assert legacy_report.read_text(encoding="utf-8") == "keep this old report"


@pytest.mark.asyncio
async def test_zero_round_budget_stops_continue_without_report(tmp_path, external_io):
    config = _config(tmp_path, minimum=2, max_rounds=0)

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_BUDGET"
    assert result.failed_stage is None
    assert result.report_result is None
    assert not list(result.topic_dir.glob("runs/*/final/report.md"))
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is False
    assert "targeted" not in result.stages_completed
    assert "merge" not in result.stages_completed
