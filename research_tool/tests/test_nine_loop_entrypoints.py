import json
import hashlib
from pathlib import Path

import pytest

from typer.testing import CliRunner

import research_tool
from research_tool.infrastructure.export.wiki_stage import validate_current_run
from research_tool.nine_loop.runtime_state import RuntimeState
from research_tool.presentation.cli import _build_run_overrides, app
from research_tool.application.pipeline import ResearchPipeline
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_nine_loop_runner import config, model_reply, QUOTE


STAGES = "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report"
STAGE_NAMES = STAGES.split(" → ")


@pytest.mark.parametrize("damage", [False, True])
def test_cli_validates_committed_report_before_success(tmp_path, monkeypatch, damage):
    cfg = config(tmp_path, minimum=1)
    cfg.collector.strategies.enabled = False
    cfg.input_dir = tmp_path / "input"
    cfg.input_dir.mkdir()
    (cfg.input_dir / "paper.md").write_text(
        "<!-- source: file:///approved/paper.md -->\n\n" + QUOTE, encoding="utf-8"
    )

    class CompletionBoundary(ResearchPipeline):
        async def stream(self, topic=None):
            async for event in super().stream(topic):
                yield event
            if damage and self._result.report_result:
                self._result.report_result.report_path.unlink()

    monkeypatch.setattr("research_tool.presentation.cli.load_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr("research_tool.presentation.cli.ResearchPipeline", CompletionBoundary)
    monkeypatch.setattr(
        "research_tool.infrastructure.llm.base.LLMClient.from_config",
        lambda *_a: MockLLMClient(structured_response=model_reply),
    )
    result = CliRunner().invoke(app, ["run", cfg.topic])

    assert (result.exit_code != 0) is damage, result.output


def test_cli_dry_run_lists_the_complete_nine_stage_chain(monkeypatch):
    class Config:
        mode = "standard"
        stages = []

    monkeypatch.setattr("research_tool.presentation.cli.load_config", lambda *_a, **_k: Config())
    result = CliRunner().invoke(app, ["run", "topic", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert STAGES in result.output


def test_cli_rejects_partial_stages_and_brief_mode(monkeypatch):
    class Config:
        mode = "standard"
        stages = []

    monkeypatch.setattr("research_tool.presentation.cli.load_config", lambda *_a, **_k: Config())
    runner = CliRunner()

    skipped = runner.invoke(app, ["run", "topic", "--skip", "extract", "--dry-run"])
    brief = runner.invoke(app, ["run", "topic", "--mode", "brief", "--dry-run"])
    fast = runner.invoke(app, ["run", "topic", "--mode", "fast", "--dry-run"])

    assert skipped.exit_code != 0
    assert brief.exit_code != 0
    assert fast.exit_code != 0


def test_status_does_not_infer_completion_from_report_file(tmp_path):
    (tmp_path / "report.md").write_text("stale report", encoding="utf-8")

    result = CliRunner().invoke(app, ["status", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "✓ 完成" not in result.output


def test_status_requires_complete_summary_and_runtime_stage_evidence(tmp_path):
    topic = _make_current_run(tmp_path)

    result = CliRunner().invoke(app, ["status", str(topic)], terminal_width=160)

    assert result.exit_code == 0, result.output
    assert result.output.count("✓ 完成") == len(STAGE_NAMES)
    assert not (topic / "runtime-state.lock").exists()


def _make_current_run(root: Path) -> Path:
    topic = root / "topic"
    topic.mkdir()
    runtime = RuntimeState(topic)
    state = runtime.start_new("current-input")
    committed = ("collect", "clean", "extract", "knowledge",
                 "inspect:0", "qgate:0", "report")
    for stage in committed:
        artifact = topic / "artifacts" / state["run_id"] / f"{stage.replace(':', '-')}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        payload = stage.encode()
        artifact.write_bytes(payload)
        state = runtime.commit_stage(
            stage, artifact, hashlib.sha256(payload).hexdigest(), state["latest_digest"]
        )
    final = topic / "runs" / state["run_id"] / "final"
    tree = final / "tree"
    tree.mkdir(parents=True)
    for relative, content in {
        "report.md": b"current report",
        "sources.json": b"[]",
        "tree/00-主表.md": b"# tree",
        "tree/knowledge.json": b"{}",
    }.items():
        target = final / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    summary = {
        "version": 1,
        "run_id": state["run_id"],
        "verdict": "STOP_SUCCESS",
        "pipeline_complete": True,
        "output_contract": "nine-stage",
        "output_dir": f"runs/{state['run_id']}/final",
        "files": {
            path.relative_to(topic).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(final.rglob("*")) if path.is_file()
        },
        "stages": STAGE_NAMES,
        "stages_completed": [
            "collect", "clean", "extract", "knowledge", "inspect", "qgate", "report"
        ],
        "stages_skipped": ["targeted", "merge"],
    }
    (topic / "run-summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (topic / "runtime-state.lock").unlink()
    return topic


def test_status_rejects_bad_run_id_digest_and_missing_final_output(tmp_path: Path) -> None:
    topic = _make_current_run(tmp_path)
    summary_path = topic / "run-summary.json"
    original = json.loads(summary_path.read_text())
    cases = (
        {**original, "run_id": "0" * 32},
        {**original, "files": {**original["files"],
                                  next(iter(original["files"])): "0" * 64}},
    )
    for summary in cases:
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        result = CliRunner().invoke(app, ["status", str(topic)], terminal_width=160)
        assert result.exit_code == 0, result.output
        assert "✓ 完成" not in result.output
    summary_path.write_text(json.dumps(original), encoding="utf-8")
    output = topic / original["output_dir"]
    (output / "report.md").unlink()
    result = CliRunner().invoke(app, ["status", str(topic)], terminal_width=160)
    assert result.exit_code == 0, result.output
    assert "✓ 完成" not in result.output
    assert not (topic / "runtime-state.lock").exists()


def test_current_run_resolver_binds_run_id_files_and_hashes(tmp_path: Path) -> None:
    topic = _make_current_run(tmp_path)
    summary, state, output = validate_current_run(topic)
    assert summary["run_id"] == state["run_id"]
    assert output == topic / summary["output_dir"]
    assert not (topic / "runtime-state.lock").exists()


def test_cli_maps_existing_input_and_round_limit_to_nine_loop(tmp_path):
    overrides = _build_run_overrides(
        topic="topic",
        mode="standard",
        output=None,
        input_dir=tmp_path / "raw",
        resume=True,
        source=[],
        max_results=8,
        rounds=None,
        llm_expand=False,
        query=[],
        core=None,
        facets=None,
        from_year=None,
        to_year=None,
        deep_search=None,
        deep_pages=None,
        deep_sorts=None,
        search_relevance_min_overlap=None,
        x_backend=None,
        x_cmd=None,
        relevance_filter=False,
        max_rounds=4,
        pdf_dir=None,
        mineru_cmd=None,
        ocr_engine=None,
        ocr_cmd=None,
        ocr_model_path=None,
        translate=False,
        model=None,
    )

    assert overrides["stages"] == STAGES.split(" → ")
    assert overrides["input_dir"] == str(tmp_path / "raw")
    assert overrides["nine_loop"] == {"max_rounds": 4}
    assert "max_backward_rounds" not in overrides


def test_partial_research_commands_and_quick_collect_are_not_public():
    command_names = {command.name for command in app.registered_commands}

    assert not command_names.intersection({"collect", "clean", "extract", "organize", "report"})
    assert not hasattr(research_tool, "quick_collect")
    assert "quick_collect" not in research_tool.__all__
    assert not hasattr(research_tool, "Reporter")
    assert "Reporter" not in research_tool.__all__


def test_cli_help_and_dispatch_exclude_removed_web_interface():
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0, help_result.output
    command_names = {command.name or command.callback.__name__ for command in app.registered_commands}
    assert "run" in command_names and "ui" not in command_names
    removed = runner.invoke(app, ["ui"])
    assert removed.exit_code == 2
    assert "No such command" in removed.output
