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

pytest_plugins = ["research_tool.tests.test_nine_loop_runner"]


def _body_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fixture_run(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "battery"
    run_id = "a" * 32
    raw = root / "runs" / run_id / "initial" / "raw"
    final_tree = root / "runs" / run_id / "final" / "tree"
    raw.mkdir(parents=True)
    final_tree.mkdir(parents=True)

    body = "The measured battery retained 90 percent capacity after 1000 cycles.\n"
    source = (
        "<!-- source: https://papers.test/battery -->\n"
        "<!-- title: Battery cycle life -->\n"
        "<!-- fetched: 2026-10-18T08:00:00Z -->\n"
        "<!-- evidence_kind: body -->\n\n"
        + body
    )
    source_path = raw / "01-papers.test.md"
    source_path.write_text(source, encoding="utf-8")
    (raw / "sources.json").write_text(
        json.dumps(
            [
                {
                    "url": "https://papers.test/battery",
                    "title": "Battery cycle life",
                    "fetched_at": "2026-10-18T08:00:00Z",
                    "source_engine": "openalex",
                    "content_hash": _body_hash(body),
                }
            ]
        ),
        encoding="utf-8",
    )
    (raw / "source-audit.json").write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
    )
    (raw / "search-audit.json").write_text(
        json.dumps(
            {
                "version": 1,
                "planned_query_count": 2,
                "planned_request_count": 2,
                "actual_attempt_count": 2,
                "successful_hit_count": 5,
                "failed_request_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (root / "runtime-state.json").write_text(
        json.dumps({"input_fingerprint": "sha256:input"}), encoding="utf-8"
    )
    (root / "runs" / run_id / "final" / "report.md").write_text(
        "# Battery\n", encoding="utf-8"
    )
    (root / "runs" / run_id / "final" / "sources.json").write_text("[]", encoding="utf-8")
    (root / "runs" / run_id / "final" / "tree" / "knowledge.json").write_text(
        "{}", encoding="utf-8"
    )
    summary = {
        "run_id": run_id,
        "verdict": "STOP_SUCCESS",
        "pipeline_complete": True,
        "rounds": 0,
        "failed_stage": None,
        "stages": [
            "collect",
            "clean",
            "extract",
            "knowledge",
            "inspect",
            "targeted",
            "merge",
            "qgate",
            "report",
        ],
        "stages_completed": ["collect", "clean", "extract", "knowledge", "inspect", "qgate", "report"],
        "stages_skipped": ["targeted", "merge"],
    }
    return root, summary


def test_manifest_separates_body_and_artifact_hashes(tmp_path):
    root, summary = _fixture_run(tmp_path)

    manifest = build_retrieval_manifest(root, summary)

    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["pipeline_complete"] is True
    assert manifest["input_fingerprint"] == "sha256:input"
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
    assert source["raw_path"] == f"runs/{summary['run_id']}/initial/raw/01-papers.test.md"
    assert source["body_sha256"].startswith("sha256:")
    assert source["artifact_sha256"].startswith("sha256:")
    assert source["body_sha256"] != source["artifact_sha256"]
    assert not Path(source["raw_path"]).is_absolute()


def test_manifest_write_is_atomic_and_reusable(tmp_path):
    root, summary = _fixture_run(tmp_path)

    path = write_retrieval_manifest(root, summary)

    assert path == root / "runs" / summary["run_id"] / "retrieval-manifest.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == summary["run_id"]
    assert not list(path.parent.glob(".retrieval-manifest-*"))


@pytest.mark.asyncio
async def test_pipeline_writes_retrieval_manifest_at_run_close(tmp_path, external_io):
    config = runner_config(tmp_path, minimum=1)
    config.collector.strategies.enabled = False
    config.llm_retry_backoff_sec = 0

    result = await ResearchPipeline(
        config, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
    manifest_path = result.topic_dir / "runs" / summary["run_id"] / "retrieval-manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["topic"] == "battery"
    assert manifest["pipeline_complete"] is True
    assert manifest["sources"]


def test_manifest_rejects_changed_source_body(tmp_path):
    root, summary = _fixture_run(tmp_path)
    source_path = next((root / "runs" / summary["run_id"] / "initial" / "raw").glob("*.md"))
    source_path.write_text(source_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source body hash mismatch"):
        build_retrieval_manifest(root, summary)
