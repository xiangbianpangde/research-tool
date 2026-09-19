"""Gate 1 defect remediation unit tests.

Verifies:
1. OpenAILLMClient.chat() auto-continuation loop on finish_reason == 'length'
2. Reporter fallback to topic_dir/sources.json if raw/sources.json is empty or missing
3. Reporter reference regex stripping and sentence boundary defense
4. Config LLM_MAX_TOKENS env override and full mode max_tokens=16384 default
5. Pipeline _finish_run stage merging on resume
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest

from research_tool.domain.config import load_config
from research_tool.domain.models import (
    LLMConfig,
    PipelineConfig,
    PipelineResult,
    ReporterConfig,
    StageRunMetric,
)
from research_tool.infrastructure.llm.openai_client import OpenAILLMClient
from research_tool.infrastructure.stages.reporter import Reporter
from research_tool.application.pipeline import ResearchPipeline
from research_tool.infrastructure.stages.base import write_json, read_json


@pytest.mark.asyncio
async def test_openai_client_auto_continuation():
    """Verify OpenAILLMClient automatically continues generation when finish_reason=='length'."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")

    mock_choice_1 = MagicMock()
    mock_choice_1.finish_reason = "length"
    mock_choice_1.message.content = "第一部分内容，被截断处："

    mock_choice_2 = MagicMock()
    mock_choice_2.finish_reason = "stop"
    mock_choice_2.message.content = "接续的内容，顺利结束。"

    mock_resp_1 = MagicMock()
    mock_resp_1.choices = [mock_choice_1]

    mock_resp_2 = MagicMock()
    mock_resp_2.choices = [mock_choice_2]

    client = OpenAILLMClient(cfg)
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(
        side_effect=[mock_resp_1, mock_resp_2]
    )

    result = await client.chat("请生成长篇报告")
    assert result == "第一部分内容，被截断处：接续的内容，顺利结束。"
    assert client._client.chat.completions.create.call_count == 2


def test_config_max_tokens_full_mode_and_env():
    """Verify full mode default max_tokens=16384 and LLM_MAX_TOKENS env override."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_MAX_TOKENS", None)
        cfg = load_config(overrides={"mode": "full"})
        assert cfg.llm.max_tokens == 16384

    with patch.dict(os.environ, {"LLM_MAX_TOKENS": "8192"}):
        cfg2 = load_config(overrides={"mode": "full"})
        assert cfg2.llm.max_tokens == 8192


@pytest.mark.asyncio
async def test_reporter_source_fallback(tmp_path: Path):
    """Verify _load_sources falls back to topic_dir/sources.json if raw/sources.json is empty."""
    tree_dir = tmp_path / "tree"
    tree_dir.mkdir(parents=True)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)

    # raw/sources.json is empty
    write_json(raw_dir / "sources.json", [])

    # root sources.json has 1 source
    write_json(
        tmp_path / "sources.json",
        [{"title": "Fallback Source", "url": "https://example.com/source"}],
    )

    reporter = Reporter(ReporterConfig())
    sources = reporter._load_sources(tree_dir)
    assert len(sources) == 1
    assert sources[0]["title"] == "Fallback Source"
    assert sources[0]["url"] == "https://example.com/source"


@pytest.mark.asyncio
async def test_reporter_regex_split_and_sentence_defense(tmp_path: Path):
    """Verify various reference headings are stripped and trailing dangling clauses are cleaned."""
    tree_dir = tmp_path / "tree"
    tree_dir.mkdir(parents=True)
    (tree_dir / "00-主表.md").write_text("# 主表\n", encoding="utf-8")
    (tree_dir / "N1.md").write_text("节点内容\n", encoding="utf-8")

    mock_llm = MagicMock()
    # Body has dangling clause followed by alternative reference title
    mock_llm.chat = AsyncMock(
        return_value=(
            "这是完整的第一段话。\n\n"
            "这是未完成的句子：要同步\n\n"
            "## 参考文献\n"
            "- [1] Some Paper\n"
        )
    )

    reporter = Reporter(ReporterConfig())
    out_file = tmp_path / "report.md"
    await reporter.run(tree_dir, mock_llm, topic="测试主题", output_path=out_file)

    content = out_file.read_text(encoding="utf-8")
    # Dangling fragment should have been trimmed to the last period
    assert "要同步" not in content
    assert "这是完整的第一段话。" in content
    assert "## 参考文献" not in content  # stripped model's heading


def test_pipeline_resume_stage_merging(tmp_path: Path):
    """Verify _finish_run merges stages_completed from state.json when resume=True."""
    topic_dir = tmp_path / "test-topic"
    topic_dir.mkdir(parents=True)

    # Write state.json with 9 completed stages
    nine_stages = [
        "collect", "clean", "extract", "knowledge", "inspect",
        "targeted", "merge", "qgate", "report",
    ]
    write_json(topic_dir / "state.json", {"done": nine_stages})

    cfg = PipelineConfig(
        topic="test-topic",
        mode="full",
        work_dir=str(tmp_path),
        resume=True,
    )
    pipeline = ResearchPipeline(cfg)

    # Simulate a run where all stages were skipped
    result = PipelineResult(
        topic="test-topic",
        topic_dir=topic_dir,
        stages_completed=[],
        stages_skipped=list(nine_stages),
        stage_metrics=[
            StageRunMetric(stage=s, status="skipped", duration_sec=0.0)
            for s in nine_stages
        ],
    )

    pipeline._finish_run(result, started_at=100.0)

    summary = read_json(topic_dir / "run-summary.json")
    assert summary["pipeline_complete"] is True
    assert summary["stages_completed"] == nine_stages
    assert summary["stages_skipped"] == []
