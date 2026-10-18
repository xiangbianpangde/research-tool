from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_nine_loop_runner import model_reply, config as runner_config
from research_tool.infrastructure.retrieval.manifest import (
    build_retrieval_manifest,
    write_retrieval_manifest,
)
from research_tool.nine_loop.evidence import parse_source

pytest_plugins = ["research_tool.tests.test_nine_loop_runner"]

STAGES = [
    "collect", "clean", "extract", "knowledge", "inspect",
    "targeted", "merge", "qgate", "report",
]


def _bare_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _body_hash(text: str) -> str:
    return "sha256:" + _bare_hash(text.encode("utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _fixture_run(tmp_path: Path, *, run_id: str = "a" * 32) -> tuple[Path, dict]:
    root = tmp_path / "battery"
    run_root = root / "runs" / run_id
    raw = run_root / "initial" / "raw"
    clean = run_root / "initial" / "clean"
    final_tree = run_root / "final" / "tree"
    artifact_dir = root / "artifacts" / run_id
    raw.mkdir(parents=True)
    clean.mkdir(parents=True)
    final_tree.mkdir(parents=True)
    artifact_dir.mkdir(parents=True)

    body = "The measured battery retained 90 percent capacity after 1000 cycles.\n"
    source = (
        "<!-- source: https://papers.test/battery -->\r\n"
        "<!-- title: Battery cycle life -->\r\n"
        "<!-- fetched: 2026-10-18T08:00:00Z -->\r\n"
        "<!-- evidence_kind: body -->\r\n\r\n"
        + body.replace("\n", "\r\n")
    )
    source_path = raw / "01-papers.test.md"
    source_path.write_bytes(source.encode("utf-8"))
    clean_path = clean / source_path.name
    clean_path.write_text(source.replace("\r\n", "\n"), encoding="utf-8")
    raw_parsed = parse_source(source_path.read_text(encoding="utf-8"))
    parsed = parse_source(clean_path.read_text(encoding="utf-8"))
    source_id = parsed["source_id"]
    locator = parsed["locator"]
    _write_json(
        raw / "sources.json",
        [
            {
                "url": locator,
                "title": "Battery cycle life",
                "fetched_at": "2026-10-18T08:00:00Z",
                "source_engine": "openalex",
                "content_hash": _body_hash(raw_parsed["text"]),
            }
        ],
    )
    _write_json(
        raw / "source-audit.json",
        {
            "version": 1,
            "sources": [
                {
                    "engine": "openalex",
                    "attempted": 2,
                    "hits": 5,
                    "failed": 1,
                    "filtered": 1,
                    "deduplicated": 1,
                    "fetch_failed": 0,
                    "retained": 1,
                }
            ],
        },
    )
    _write_json(
        raw / "search-audit.json",
        {
            "version": 1,
            "planned_query_count": 2,
            "planned_request_count": 2,
            "actual_attempt_count": 2,
            "successful_hit_count": 5,
            "failed_request_count": 1,
            "calls": [
                {
                    "queries": ["battery", "battery cycles"],
                    "planned_request_count": 2,
                    "actual_attempt_count": 2,
                    "successful_hit_count": 5,
                    "failed_request_count": 1,
                }
            ],
        },
    )

    final_sources = [
        {
            "source_id": source_id,
            "locator": locator,
            "title": "Battery cycle life",
            "text": parsed["text"],
            "content_sha256": parsed["content_sha256"],
            "path": str(clean_path),
        }
    ]
    (run_root / "final" / "report.md").write_text("# Battery\n", encoding="utf-8")
    _write_json(run_root / "final" / "sources.json", final_sources)
    (run_root / "final" / "tree" / "00-主表.md").write_text("# Battery\n", encoding="utf-8")
    _write_json(run_root / "final" / "tree" / "knowledge.json", {"sources": final_sources})

    envelope = {
        "v": 1,
        "run_id": run_id,
        "stage": "collect",
        "request_id": "collect",
        "idempotency_key": "e" * 64,
        "budget_lease": {"wall_s_max": 10},
        "result": {"output_files": [str(source_path)]},
        "error": None,
        "files": {f"runs/{run_id}/initial/raw/{source_path.name}": _bare_hash(source_path.read_bytes())},
    }
    envelope_path = artifact_dir / "collect.json"
    _write_json(envelope_path, envelope)
    runtime = {
        "version": 1,
        "run_id": run_id,
        "input_fingerprint": "b" * 64,
        "round": 0,
        "completed_stages": {
            "collect": {
                "artifact_path": f"artifacts/{run_id}/collect.json",
                "artifact_digest": _bare_hash(envelope_path.read_bytes()),
            }
        },
        "latest_digest": _bare_hash(envelope_path.read_bytes()),
    }
    record = {
        "version": 1,
        "run_id": run_id,
        "topic": "battery",
        "topic_slug": "battery",
        "verdict": "STOP_SUCCESS",
        "pipeline_complete": True,
        "started_at": "2026-10-18T08:00:00Z",
        "finished_at": "2026-10-18T08:05:00Z",
        "input_fingerprint": "b" * 64,
        "output_contract": "nine-stage",
        "stages": STAGES,
        "stages_completed": ["collect", "clean", "extract", "knowledge", "inspect", "qgate", "report"],
        "stages_skipped": ["targeted", "merge"],
        "failed_stage": None,
        "files": {
            f"runs/{run_id}/final/report.md": _bare_hash((run_root / "final" / "report.md").read_bytes()),
            f"runs/{run_id}/final/sources.json": _bare_hash((run_root / "final" / "sources.json").read_bytes()),
            f"runs/{run_id}/final/tree/00-主表.md": _bare_hash((run_root / "final" / "tree/00-主表.md").read_bytes()),
            f"runs/{run_id}/final/tree/knowledge.json": _bare_hash((run_root / "final" / "tree/knowledge.json").read_bytes()),
        },
        "runtime": runtime,
    }
    _write_json(run_root / "run-record.json", record)
    summary = {
        "run_id": run_id,
        "verdict": "STOP_SUCCESS",
        "pipeline_complete": True,
        "stages": STAGES,
        "stages_completed": record["stages_completed"],
        "stages_skipped": record["stages_skipped"],
    }
    return root, summary


def test_manifest_separates_body_and_artifact_hashes_and_records_all_files(tmp_path):
    root, summary = _fixture_run(tmp_path)

    manifest = build_retrieval_manifest(root, summary)

    assert manifest["schema"] == 1
    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["pipeline_complete"] is True
    assert manifest["input_fingerprint"] == "b" * 64
    assert manifest["record_path"] == f"runs/{summary['run_id']}/run-record.json"
    assert manifest["record_sha256"].startswith("sha256:")
    assert manifest["stages"]["targeted"] == "skipped"
    assert manifest["search"] == {
        "planned_query_count": 2,
        "planned_request_count": 2,
        "actual_attempt_count": 2,
        "successful_hit_count": 5,
        "retained_body_source_count": 1,
        "failed_request_count": 1,
    }
    assert len(manifest["sources"]) == 1

    source = manifest["sources"][0]
    raw_path = root / source["raw_path"]
    clean_path = root / manifest["citation_sources"][0]["clean_path"]
    raw_parsed = parse_source(raw_path.read_text(encoding="utf-8"))
    parsed = parse_source(clean_path.read_text(encoding="utf-8"))
    assert source["raw_path"] == f"runs/{summary['run_id']}/initial/raw/01-papers.test.md"
    assert source["body_sha256"].startswith("sha256:")
    assert source["artifact_sha256"].startswith("sha256:")
    assert source["body_sha256"] != source["artifact_sha256"]
    assert manifest["artifact_hashes"]["report"].startswith("sha256:")
    assert manifest["citation_sources"][0]["clean_path"].endswith("initial/clean/01-papers.test.md")
    assert manifest["citation_sources"][0]["clean_hash"] == (
        "sha256:" + parsed["content_sha256"]
    )
    assert manifest["citation_sources"][0]["raw_hash"] == (
        "sha256:" + raw_parsed["content_sha256"]
    )
    assert all(value.startswith("sha256:") for value in manifest["files"].values())
    assert f"artifacts/{summary['run_id']}/collect.json" in manifest["files"]
    assert source["raw_path"] not in {str(Path(source["raw_path"]).resolve())}


def test_manifest_write_is_atomic_and_reusable(tmp_path):
    root, summary = _fixture_run(tmp_path)

    path = write_retrieval_manifest(root, summary)

    assert path == root / "runs" / summary["run_id"] / "retrieval-manifest.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == summary["run_id"]
    assert not list(path.parent.glob(".retrieval-manifest-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_mode", ["error", "budget", "cancelled"])
async def test_non_success_run_writes_record_and_manifest_without_final(
    tmp_path, external_io, stop_mode, monkeypatch
):
    config = runner_config(tmp_path, minimum=2)
    config.collector.strategies.enabled = False
    config.llm_retry_backoff_sec = 0
    if stop_mode == "budget":
        config.nine_loop.max_rounds = 0
    if stop_mode == "cancelled":
        import asyncio

        class WaitingLLM(MockLLMClient):
            async def chat_structured(self, prompt, schema, system=None):
                await asyncio.Event().wait()

        pipeline = ResearchPipeline(config, llm=WaitingLLM())
        task = asyncio.create_task(pipeline.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = pipeline._result
    else:
        if stop_mode == "error":
            monkeypatch.setattr(
                "research_tool.application.pipeline.ResearchPipeline._collect",
                lambda self: (_ for _ in ()).throw(RuntimeError("fixture failure")),
            )
        result = await ResearchPipeline(
            config, llm=MockLLMClient(structured_response=model_reply)
        ).run()

    assert result.verdict == {
        "error": "STOP_ERROR", "budget": "STOP_BUDGET", "cancelled": "STOP_CANCELLED"
    }[stop_mode]
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    run_root = result.topic_dir / "runs" / summary["run_id"]
    record = json.loads((run_root / "run-record.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_root / "retrieval-manifest.json").read_text(encoding="utf-8"))
    assert record["verdict"] == result.verdict
    assert record["pipeline_complete"] is False
    assert record["files"] == {}
    assert manifest["verdict"] == result.verdict
    assert manifest["pipeline_complete"] is False
    assert manifest["artifacts"] == {}
    assert manifest["citation_sources"] == []
    assert not (run_root / "final" / "report.md").exists()


@pytest.mark.asyncio
async def test_pipeline_writes_retrieval_manifest_and_record_at_run_close(tmp_path, external_io):
    config = runner_config(tmp_path, minimum=1)
    config.collector.strategies.enabled = False
    config.llm_retry_backoff_sec = 0

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    run_root = result.topic_dir / "runs" / summary["run_id"]
    record = json.loads((run_root / "run-record.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_root / "retrieval-manifest.json").read_text(encoding="utf-8"))
    assert record["verdict"] == "STOP_SUCCESS"
    assert record["input_fingerprint"] == summary["input_fingerprint"]
    assert set(record["files"]) == {
        f"runs/{summary['run_id']}/final/report.md",
        f"runs/{summary['run_id']}/final/sources.json",
        f"runs/{summary['run_id']}/final/tree/00-主表.md",
        f"runs/{summary['run_id']}/final/tree/knowledge.json",
    }
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["topic"] == "battery"
    assert manifest["pipeline_complete"] is True
    assert manifest["sources"]


def test_manifest_requires_the_run_record_and_does_not_use_root_state(tmp_path):
    root, summary = _fixture_run(tmp_path)
    record_path = root / "runs" / summary["run_id"] / "run-record.json"
    record_path.unlink()
    (root / "runtime-state.json").write_text(
        json.dumps({"input_fingerprint": "f" * 64}), encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError):
        build_retrieval_manifest(root, summary)


def test_manifest_rejects_changed_source_body(tmp_path):
    root, summary = _fixture_run(tmp_path)
    source_path = next((root / "runs" / summary["run_id"] / "initial" / "raw").glob("*.md"))
    source_path.write_text(source_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source body hash mismatch"):
        build_retrieval_manifest(root, summary)
