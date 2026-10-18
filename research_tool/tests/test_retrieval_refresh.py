from __future__ import annotations

import json
from pathlib import Path

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
