from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import PipelineConfig, RetrievalConfig


def _pipeline(tmp_path: Path) -> ResearchPipeline:
    config = PipelineConfig(
        topic="battery",
        work_dir=tmp_path,
        retrieval=RetrievalConfig(refresh_on_run=True),
    )
    pipeline = ResearchPipeline(config)
    pipeline.root = tmp_path / "battery"
    pipeline.root.mkdir(parents=True)
    return pipeline


@pytest.mark.asyncio
async def test_retrieval_refresh_settings_do_not_change_research_identity(tmp_path, monkeypatch):
    first = ResearchPipeline(
        PipelineConfig(topic="battery", work_dir=tmp_path, retrieval=RetrievalConfig())
    )
    second = ResearchPipeline(
        PipelineConfig(
            topic="battery",
            work_dir=tmp_path,
            retrieval=RetrievalConfig(
                refresh_on_run=True,
                module_root=tmp_path / "researchctl",
                timeout_sec=120,
            ),
        )
    )
    # Run-level behavior: changing only derived refresh settings must reuse the
    # existing run identity, not merely produce equal hand-written dictionaries.
    monkeypatch.setattr(
        "research_tool.application.pipeline.ResearchPipeline._refresh_retrieval_index",
        lambda self, summary: None,
    )
    first_result = await first.run()
    second_result = await second.run()
    first_id = json.loads(first_result.run_summary_path.read_text())["run_id"]
    second_id = json.loads(second_result.run_summary_path.read_text())["run_id"]
    assert first_id == second_id


def test_refresh_success_records_derived_state(monkeypatch, tmp_path):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def index(self):
            return {"status": "success", "query_type": "reindex"}

        def generate_index(self):
            return {"status": "success", "query_type": "status"}

    monkeypatch.setattr(
        "research_tool.infrastructure.retrieval.ResearchCtlClient", FakeClient
    )
    pipeline = _pipeline(tmp_path)
    summary = {"run_id": "a" * 32, "verdict": "STOP_SUCCESS"}

    pipeline._refresh_retrieval_index(summary)

    state_path = pipeline.root / ".index" / "retrieval-refresh" / "battery.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["run_id"] == summary["run_id"]
    assert state["index"]["query_type"] == "reindex"
    assert state["navigation"]["query_type"] == "status"
    assert calls


def test_refresh_failure_records_stale_without_raising(monkeypatch, tmp_path):
    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        def index(self):
            raise RuntimeError("fixture index unavailable")

    monkeypatch.setattr(
        "research_tool.infrastructure.retrieval.ResearchCtlClient", BrokenClient
    )
    pipeline = _pipeline(tmp_path)
    summary = {"run_id": "b" * 32, "verdict": "STOP_ERROR"}

    pipeline._refresh_retrieval_index(summary)

    state_path = pipeline.root / ".index" / "retrieval-refresh" / "battery.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "stale"
    assert state["run_id"] == summary["run_id"]
    assert state["error_type"] == "RuntimeError"
    assert "fixture index unavailable" in state["error"]
