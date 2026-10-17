"""V1.1 VideoIngest 集成入口层端到端测试（mock 模式）。

覆盖：
- 端到端：mock download → transcribe → summarize → write_markdown → trigger_pipeline
- CLI 入口：`research run "主题" --video-url "URL"` 走通
- 多 URL 并发调度
- URL 白名单 + 非白名单拒绝
- 下游九段入口触发契约和本地 Markdown 输入的实际消费/恢复
- 产物验证：<topic>/raw/video_<video_id>.md 文件结构

所有外部依赖（yt-dlp / whisper / LLM）都 mock 掉。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from research_tool.application.video_pipeline import (
    VideoPipeline,
    validate_video_url,
)
from research_tool.common.slug import slugify
from research_tool.domain.errors import ErrorCode, ResearchToolError, VideoIngestError
from research_tool.domain.models import (
    NINE_STAGES,
    CleanerConfig,
    LLMSummary,
    OrganizerConfig,
    PipelineConfig,
    Transcript,
    TranscriptSegment,
    VideoURL,
)
from research_tool.infrastructure.ingest.downloader import DownloadResult
from research_tool.infrastructure.ingest.pipeline_adapter import (
    VIDEO_FILENAME_PREFIX,
    StagesResult,
)
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.evidence import parse_source
from research_tool.presentation.cli import app


@pytest.fixture(autouse=True)
def _isolate_cli_configuration(monkeypatch):
    """入口测试不用本机 config.yaml、环境密钥或真实模型客户端。"""
    def config(*args, overrides=None, **kwargs):
        return PipelineConfig(**(overrides or {}))

    monkeypatch.setattr("research_tool.presentation.cli.load_config", config)
    monkeypatch.setattr(
        "research_tool.infrastructure.llm.base.LLMClient.from_config",
        lambda cfg: pytest.fail("unexpected real LLM client"),
    )


# --------------------------------------------------------------------------- #
# Mock helpers
# --------------------------------------------------------------------------- #


def make_fake_download_result(
    tmp_path: Path,
    video_id: str = "BV1xx411c7mD",
    platform: str = "bilibili",
    title: str = "测试视频标题",
    duration_sec: int = 120,
    file_suffix: str = "m4a",
) -> DownloadResult:
    """构造假 DownloadResult。"""
    fake_path = tmp_path / f"{video_id}.{file_suffix}"
    fake_path.write_bytes(b"fake audio")
    return DownloadResult(
        file_path=str(fake_path),
        size_mb=1.5,
        duration_sec=duration_sec,
        video_id=video_id,
        etag="",
        platform=platform,
        title=title,
        cover_url="https://i0.hdslb.com/cover.jpg",
    )


def make_fake_transcript() -> Transcript:
    """构造假 Transcript。"""
    return Transcript(
        language="zh",
        full_text="大家好欢迎收看本期视频，今天我们来聊一聊人工智能",
        segments=[
            TranscriptSegment(start=0.0, end=1.5, text="大家好欢迎收看本期视频"),
            TranscriptSegment(start=1.5, end=3.0, text="今天我们来聊一聊人工智能"),
        ],
        engine="whisper",
        cer_estimate=0.05,
    )


def make_fake_summary() -> LLMSummary:
    """构造假 LLMSummary。"""
    from research_tool.domain.models import Chapter

    return LLMSummary(
        video_summary="本视频介绍了 AI 的基本概念和发展历程。",
        video_chapters=[
            Chapter(start_sec=0, end_sec=60, title="开场介绍"),
            Chapter(start_sec=60, end_sec=120, title="AI 发展史"),
        ],
        video_takeaways=[
            "了解 AI 的基本概念",
            "掌握 AI 的核心技术",
        ],
        model="deepseek-v4-flash",
    )


# --------------------------------------------------------------------------- #
# 端到端集成：download → transcribe → summarize → 落盘 → 触发管道
# --------------------------------------------------------------------------- #


class TestEndToEndMocked:
    """完整 mock 端到端集成测试。"""

    @pytest.mark.asyncio
    async def test_bilibili_url_full_flow(self, tmp_path: Path):
        """B 站 URL → mock download → mock transcribe → mock LLM → 落 raw/<topic>/video_<id>.md。"""
        # 1) mock 各阶段函数
        fake_dl_result = make_fake_download_result(
            tmp_path,
            video_id="BV1xx411c7mD",
            platform="bilibili",
            title="AI 教程",
        )
        fake_transcript = make_fake_transcript()
        fake_summary = make_fake_summary()

        async def fake_downloader_method(url: VideoURL) -> DownloadResult:
            return fake_dl_result

        def fake_transcribe(audio_path: str, language: str) -> Transcript:
            return fake_transcript

        def fake_summarize(text: str, meta, transcript: str) -> LLMSummary:
            return fake_summary

        # 2) 构造任务函数
        from research_tool.application.video_pipeline import build_video_task_func

        task_func = build_video_task_func(
            topic="AI 教程",
            work_dir=tmp_path,
            downloader=MagicMock(download=fake_downloader_method),
            transcriber_fn=fake_transcribe,
            summarizer_fn=fake_summarize,
        )

        # 3) 跑编排（不触发真实 pipeline）
        pipeline = VideoPipeline(topic="AI 教程", work_dir=tmp_path, run_pipeline=False)
        report = await pipeline.process_urls(
            ["https://www.bilibili.com/video/BV1xx411c7mD"],
            task_func=task_func,
        )

        # 4) 验证结果
        assert report.success_count == 1
        assert report.failed_count == 0
        result = report.results[0]
        assert result.status == "success"
        assert result.video_id == "BV1xx411c7mD"

        # 5) 验证产物文件
        assert result.markdown_path is not None
        md_path = Path(result.markdown_path)
        assert md_path.exists()
        # 路径应在 raw/<topic>/video_<id>.md
        assert md_path.parent.name == "raw"
        assert md_path.name.startswith(VIDEO_FILENAME_PREFIX)
        assert md_path.name == f"{VIDEO_FILENAME_PREFIX}BV1xx411c7mD.md"

        # 6) 验证 Markdown 内容（含 YAML front matter + body）
        md_text = md_path.read_text(encoding="utf-8")
        assert md_text.startswith("---")
        parts = md_text.split("---", 2)
        assert len(parts) >= 3
        fm = yaml.safe_load(parts[1])
        # front_matter 必备 video_ 字段
        for f in ("video_title", "video_author", "video_duration", "video_platform"):
            assert f in fm, f"front_matter 缺 {f}"
        assert fm["video_platform"] == "bilibili"
        assert fm["video_id"] == "BV1xx411c7mD"
        # body 校验
        assert "本视频介绍了 AI" in md_text
        assert "## 章节" in md_text
        assert "## 关键要点" in md_text
        # 参考来源
        assert "## 参考来源" in md_text
        assert "bilibili" in md_text

    @pytest.mark.asyncio
    async def test_youtube_url_full_flow(self, tmp_path: Path):
        """YouTube URL → 同样端到端流程。"""
        fake_dl_result = make_fake_download_result(
            tmp_path,
            video_id="dQw4w9WgXcQ",
            platform="youtube",
            title="YouTube Test",
        )

        async def fake_download(url: VideoURL) -> DownloadResult:
            return fake_dl_result

        def fake_transcribe(audio_path: str, language: str) -> Transcript:
            return make_fake_transcript()

        def fake_summarize(text: str, meta, transcript: str) -> LLMSummary:
            return make_fake_summary()

        from research_tool.application.video_pipeline import build_video_task_func

        task_func = build_video_task_func(
            topic="YouTube Test",
            work_dir=tmp_path,
            downloader=MagicMock(download=fake_download),
            transcriber_fn=fake_transcribe,
            summarizer_fn=fake_summarize,
        )

        pipeline = VideoPipeline(topic="YouTube Test", work_dir=tmp_path, run_pipeline=False)
        report = await pipeline.process_urls(
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            task_func=task_func,
        )

        assert report.success_count == 1
        result = report.results[0]
        md_path = Path(result.markdown_path)
        assert md_path.name == f"{VIDEO_FILENAME_PREFIX}dQw4w9WgXcQ.md"
        md_text = md_path.read_text(encoding="utf-8")
        parts = md_text.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["video_platform"] == "youtube"

    @pytest.mark.asyncio
    async def test_multi_url_concurrent(self, tmp_path: Path):
        """多 URL 并发处理（3 个 URL 应在 Semaphore(3) 内并行）。"""
        urls = [
            "https://www.bilibili.com/video/BV1aaa",
            "https://www.bilibili.com/video/BV1bbb",
            "https://www.youtube.com/watch?v=video1",
        ]
        call_count = {"n": 0}

        async def fake_download(url: VideoURL) -> DownloadResult:
            call_count["n"] += 1
            await asyncio.sleep(0.05)  # 模拟 I/O
            return make_fake_download_result(
                tmp_path,
                video_id=url.video_id or "unknown",
                platform=url.platform,
            )

        def fake_transcribe(audio_path: str, language: str) -> Transcript:
            return make_fake_transcript()

        def fake_summarize(text: str, meta, transcript: str) -> LLMSummary:
            return make_fake_summary()

        from research_tool.application.video_pipeline import build_video_task_func

        task_func = build_video_task_func(
            topic="multi",
            work_dir=tmp_path,
            downloader=MagicMock(download=fake_download),
            transcriber_fn=fake_transcribe,
            summarizer_fn=fake_summarize,
        )

        pipeline = VideoPipeline(topic="multi", work_dir=tmp_path, run_pipeline=False)
        start = asyncio.get_event_loop().time()
        report = await pipeline.process_urls(urls, task_func=task_func)
        elapsed = asyncio.get_event_loop().time() - start

        assert report.success_count == 3
        assert call_count["n"] == 3
        # 3 并发：3 × 0.05s = ~0.15s（串行会是 0.45s+）
        # 留余量到 0.35s
        assert elapsed < 0.35

    @pytest.mark.asyncio
    async def test_downstream_nine_stage_pipeline_triggered(self, tmp_path: Path):
        """视频成功落盘后将 topic/work_dir 交给九段适配入口。"""

        async def fake_download(url: VideoURL) -> DownloadResult:
            return make_fake_download_result(tmp_path)

        def fake_transcribe(audio_path: str, language: str) -> Transcript:
            return make_fake_transcript()

        def fake_summarize(text: str, meta, transcript: str) -> LLMSummary:
            return make_fake_summary()

        from research_tool.application.video_pipeline import build_video_task_func

        task_func = build_video_task_func(
            topic="t",
            work_dir=tmp_path,
            downloader=MagicMock(download=fake_download),
            transcriber_fn=fake_transcribe,
            summarizer_fn=fake_summarize,
        )

        # 这里只验证视频编排到适配器的交接；下游实际执行见消费用例。
        mock_stages_result = StagesResult(
            stages_run=list(NINE_STAGES),
            success=True,
            duration_ms=500,
        )

        with patch(
            "research_tool.infrastructure.ingest.pipeline_adapter.trigger_pipeline",
            AsyncMock(return_value=mock_stages_result),
        ) as mock_trigger:
            pipeline = VideoPipeline(topic="t", work_dir=tmp_path, run_pipeline=True)
            report = await pipeline.process_urls(
                ["https://www.bilibili.com/video/BV1xx"],
                task_func=task_func,
            )

        # 验证 trigger 被调用
        mock_trigger.assert_called_once()
        assert mock_trigger.call_args.args == ("t",)
        assert mock_trigger.call_args.kwargs["work_dir"] == tmp_path
        assert report.success_count == 1
        assert Path(report.results[0].markdown_path).is_file()
        assert report.stages_result is mock_stages_result


# --------------------------------------------------------------------------- #
# CLI 入口集成：research run --video-url
# --------------------------------------------------------------------------- #


class TestCliVideoUrl:
    """CLI 入口测试（research run --video-url）。"""

    def test_cli_rejects_unsupported_url(self, tmp_path: Path):
        """抖音 URL 立即被白名单拒绝。"""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["run", "test topic", "--video-url", "https://www.douyin.com/video/123"],
        )
        # typer.BadParameter 退出码 2
        assert result.exit_code != 0
        # 错误信息应提及 douyin / 不支持
        assert (
            "douyin" in (result.output + str(result.exception)).lower()
            or "不支持" in (result.output + str(result.exception))
            or "E_VID_URL_REJECTED" in (result.output + str(result.exception))
        )

    def test_cli_with_video_url_dispatches_to_video_ingest(self, tmp_path: Path):
        """--video-url → 走 VideoIngest 流程（不调真实 downloader）。"""
        runner = CliRunner()
        # Mock process_videos 以避免真实下载
        mock_report = MagicMock()
        mock_report.success_count = 1
        mock_report.failed_count = 0
        mock_report.total_duration_ms = 1000
        mock_report.stages_result = None
        mock_report.results = [
            MagicMock(
                url="https://www.bilibili.com/video/BV1xx",
                status="success",
                markdown_path=tmp_path / "video.md",
                error=None,
            ),
        ]

        with patch(
            "research_tool.application.video_pipeline.process_videos",
            AsyncMock(return_value=mock_report),
        ) as mock_process:
            result = runner.invoke(
                app,
                [
                    "run",
                    "AI 教程",
                    "--video-url",
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    "-o",
                    str(tmp_path),
                ],
            )

        # process_videos 应被调用
        mock_process.assert_called_once()
        # 验证参数
        call_kwargs = mock_process.call_args.kwargs
        assert "https://www.bilibili.com/video/BV1xx411c7mD" in call_kwargs["urls"]
        # run_pipeline=True（CLI 走全流程）
        assert call_kwargs.get("run_pipeline") is True
        # exit code 0（成功）
        assert result.exit_code == 0

    def test_cli_with_multiple_video_urls(self, tmp_path: Path):
        """多次 --video-url 累积为 list。"""
        runner = CliRunner()
        mock_report = MagicMock()
        mock_report.success_count = 2
        mock_report.failed_count = 0
        mock_report.total_duration_ms = 2000
        mock_report.stages_result = None
        mock_results = MagicMock(url="x", status="success", markdown_path=None, error=None)
        mock_report.results = [mock_results, mock_results]

        with patch(
            "research_tool.application.video_pipeline.process_videos",
            AsyncMock(return_value=mock_report),
        ) as mock_process:
            result = runner.invoke(
                app,
                [
                    "run",
                    "topic",
                    "--video-url",
                    "https://www.bilibili.com/video/BV1aaa",
                    "--video-url",
                    "https://www.youtube.com/watch?v=video2",
                    "-o",
                    str(tmp_path),
                ],
            )

        call_kwargs = mock_process.call_args.kwargs
        assert len(call_kwargs["urls"]) == 2
        assert "https://www.bilibili.com/video/BV1aaa" in call_kwargs["urls"]
        assert "https://www.youtube.com/watch?v=video2" in call_kwargs["urls"]
        assert result.exit_code == 0

    def test_cli_video_url_validation_too_many(self, tmp_path: Path):
        """>10 URL 立即拒绝。"""
        runner = CliRunner()
        urls = [f"https://www.bilibili.com/video/BV{i:010d}" for i in range(11)]
        args = ["run", "t", "-o", str(tmp_path)]
        for url in urls:
            args.extend(["--video-url", url])
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            AsyncMock(side_effect=AssertionError("rejected URLs reached downloader")),
        ) as process:
            result = runner.invoke(app, args)

        assert result.exit_code != 0
        assert "超过上限 10" in result.output
        process.assert_not_called()

    def test_cli_no_video_url_falls_through_to_normal_run(self, tmp_path: Path):
        """无 --video-url → 走原 run 命令（收集搜索）。"""
        runner = CliRunner()
        # 不传 --video-url，但因缺 LLM/网络，预期失败或正常 dry-run
        result = runner.invoke(
            app,
            ["run", "AI 教程", "--dry-run", "-o", str(tmp_path)],
        )
        # dry-run 模式应该立即退出（exit 0），不调 video_ingest
        assert result.exit_code == 0
        # 输出应包含 "将执行的阶段"
        assert "将执行的阶段" in result.output or "stages" in result.output.lower()


# --------------------------------------------------------------------------- #
# URL 拒绝路径
# --------------------------------------------------------------------------- #


class TestUrlWhitelistRejection:
    """URL 白名单拒绝行为（直接通过 validate_video_url）。"""

    def test_douyin_rejected(self):
        with pytest.raises(VideoIngestError) as exc_info:
            validate_video_url("https://www.douyin.com/video/12345")
        assert exc_info.value.code == "E_VID_URL_REJECTED"

    def test_kuaishou_rejected(self):
        with pytest.raises(VideoIngestError) as exc_info:
            validate_video_url("https://v.kuaishou.com/abc")
        assert exc_info.value.code == "E_VID_URL_REJECTED"

    def test_random_url_rejected(self):
        with pytest.raises(VideoIngestError) as exc_info:
            validate_video_url("https://example.com/foo/bar")
        assert exc_info.value.code == "E_VID_URL_REJECTED"

    def test_bilibili_accepted(self):
        v = validate_video_url("https://www.bilibili.com/video/BV1xx411c7mD")
        assert v.platform == "bilibili"

    def test_youtube_accepted(self):
        v = validate_video_url("https://www.youtube.com/watch?v=abc123")
        assert v.platform == "youtube"


# --------------------------------------------------------------------------- #
# 下游消费：视频 Markdown 经 input_dir 进入唯一九段链
# --------------------------------------------------------------------------- #


def _assert_stage_artifacts_have_valid_digests(topic_dir: Path) -> None:
    state = json.loads((topic_dir / "runtime-state.json").read_text(encoding="utf-8"))
    assert state["completed_stages"]
    for record in state["completed_stages"].values():
        artifact = topic_dir / record["artifact_path"]
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == record["artifact_digest"]
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        assert envelope["v"] == 1 and envelope["error"] is None


class TestDownstreamConsumption:
    """真实本地文件和 MockLLM 经公开 run() 检查消费及恢复行为。"""

    @pytest.fixture
    async def local_video(self, tmp_path: Path):
        from research_tool.application.video_pipeline import build_video_task_func

        quote = "The measured battery retained 90 percent capacity after 1000 cycles."
        source_url = "https://www.bilibili.com/video/BV1xx"
        topic = "video consumer"
        download = make_fake_download_result(tmp_path, video_id="BV1xx", title="Battery video")
        summary = LLMSummary(
            video_summary=quote + "\n\n" + "Experimental methods and limitations. " * 8,
        )
        task = build_video_task_func(
            topic=topic, work_dir=tmp_path,
            downloader=MagicMock(download=AsyncMock(return_value=download)),
            transcriber_fn=lambda *args: Transcript(full_text=quote),
            summarizer_fn=lambda *args: summary,
        )
        path = await task(source_url)
        markdown = path.read_text(encoding="utf-8")
        assert parse_source(markdown)["locator"] == source_url
        config = PipelineConfig(
            topic=topic, work_dir=tmp_path, input_dir=str(path.parent), resume=True,
            cleaner=CleanerConfig(min_content_length=20, relevance_filter=True),
            organizer=OrganizerConfig(min_nodes=1, max_nodes=3, min_evidence_per_node=1),
        )

        def reply(prompt, schema):
            if schema.__name__ == "_ChunkResult":
                assert quote in prompt
                return schema(claims=[{"text": quote, "quote": quote, "topic": "Cycle life"}])
            if schema.__name__ == "_Scores":
                return schema(scores=[1.0])
            if schema.__name__ == "ResearchInspection":
                return schema(findings=[])
            if schema.__name__ == "ReportOutline":
                graph = json.loads(prompt.split("DATA:\n", 1)[1])
                return schema(sections=[{
                    "heading": "Cycle life",
                    "claim_ids": [fact["claim_id"] for fact in graph["facts"]],
                }])
            if schema.__name__ == "ReportSectionDraft":
                facts = json.loads(prompt.split("DATA:\n", 1)[1])
                return schema(paragraphs=[
                    {"text": fact["text"], "claim_ids": [fact["claim_id"]]}
                    for fact in facts
                ])
            if schema.__name__ == "ReportVerification":
                data = json.loads(prompt.split("DATA:\n", 1)[1])
                facts = {fact["claim_id"]: fact for fact in data["facts"]}
                return schema(checks=[{
                    "paragraph_index": index,
                    "supported": paragraph.get("kind") == "heading" and (
                        paragraph["text"] == "Cycle life"
                    ) or all(
                        paragraph["text"] == facts[claim_id]["text"]
                        for claim_id in paragraph["claim_ids"]
                    ),
                    "reason": "Paragraph matches its supplied source quote.",
                } for index, paragraph in enumerate(data["paragraphs"])])
            raise AssertionError(f"unexpected schema: {schema.__name__}")

        return config, MockLLMClient(structured_response=reply), path, markdown, quote, source_url

    @pytest.mark.asyncio
    async def test_collector_consumes_video_markdown_through_nine_stages(self, local_video):
        """raw/ 文件作为 collect 输入，正文和来源必须到达最终报告。"""
        from research_tool.application.pipeline import ResearchPipeline

        config, llm, path, markdown, quote, source_url = local_video
        topic_dir = config.work_dir / slugify(config.topic)
        prior_report = topic_dir / "report.md"
        prior_report.write_text("Prior report remains unchanged.", encoding="utf-8")
        prior_tree = topic_dir / "tree" / "N1.md"
        prior_tree.parent.mkdir()
        prior_tree.write_text("Prior knowledge remains unchanged.", encoding="utf-8")

        result = await ResearchPipeline(config, llm=llm).run()

        assert result.failed_stage is None
        assert result.verdict == "STOP_SUCCESS" and result.report_result is not None
        assert set(result.stages_completed) | set(result.stages_skipped) == set(NINE_STAGES)
        assert "collect" in result.stages_completed
        assert "collect" not in result.stages_skipped
        _assert_stage_artifacts_have_valid_digests(topic_dir)
        summary = json.loads(result.run_summary_path.read_text(encoding="utf-8"))
        output_dir = Path("runs") / summary["run_id"] / "final"
        assert summary["output_dir"] == output_dir.as_posix()
        report_path = result.report_result.report_path
        assert report_path == topic_dir / output_dir / "report.md"
        assert report_path.relative_to(topic_dir).as_posix() in summary["files"]
        for relative, digest in summary["files"].items():
            assert Path(relative).is_relative_to(output_dir)
            assert hashlib.sha256((topic_dir / relative).read_bytes()).hexdigest() == digest
        report = report_path.read_text(encoding="utf-8")
        assert quote in report and source_url in report
        assert prior_report.read_text(encoding="utf-8") == "Prior report remains unchanged."
        assert prior_tree.read_text(encoding="utf-8") == "Prior knowledge remains unchanged."
        assert path.parent == config.work_dir / slugify(config.topic) / "raw"
        assert path.read_text(encoding="utf-8") == markdown
        assert llm.calls

    @pytest.mark.asyncio
    async def test_resume_skips_verified_video_outputs(self, local_video):
        """完整运行后恢复，已核验产物保持一致且不再请求模型。"""
        from research_tool.application.pipeline import ResearchPipeline

        config, llm, path, markdown, quote, source_url = local_video
        first = await ResearchPipeline(config, llm=llm).run()
        assert first.verdict == "STOP_SUCCESS" and first.report_result is not None
        first_report = first.report_result.report_path.read_bytes()
        resumed_llm = MockLLMClient(
            structured_response=lambda *args: pytest.fail("resume called the model")
        )

        resumed = await ResearchPipeline(config, llm=resumed_llm).run()

        assert resumed.failed_stage is None and resumed.verdict == "STOP_SUCCESS"
        assert resumed.report_result is not None
        assert resumed.report_result.report_path == first.report_result.report_path
        assert resumed.report_result.report_path.read_bytes() == first_report
        assert resumed_llm.calls == []
        topic_dir = config.work_dir / slugify(config.topic)
        _assert_stage_artifacts_have_valid_digests(topic_dir)
        summary = json.loads(resumed.run_summary_path.read_text(encoding="utf-8"))
        assert summary["pipeline_complete"] is True
        output_dir = Path("runs") / summary["run_id"] / "final"
        assert summary["output_dir"] == output_dir.as_posix()
        assert resumed.report_result.report_path == topic_dir / output_dir / "report.md"
        metrics = summary["stage_metrics"]
        assert metrics
        assert all(
            metric["status"] == "skipped" and metric["duration_sec"] == 0
            for metric in metrics
        )
        assert path.read_text(encoding="utf-8") == markdown


# --------------------------------------------------------------------------- #
# M-010 错误系统 CLI 接线（R9）：VideoIngestError → 3 段式 + 仲裁退出码
# --------------------------------------------------------------------------- #


class TestCliM010ErrorWiring:
    """VideoIngestError 经 _run → _fail_video_ingest 渲染 3 段式 + 仲裁退出码。

    覆盖 5 个退出码（403/401/404/400/500）+ 通用 ResearchToolError（exit 1）
    + 未注册错误码降级（exit 1，不崩溃处理器）。
    """

    _BILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"

    @staticmethod
    def _make_raise(exc: Exception):
        async def _raise(*args, **kwargs):
            raise exc

        return _raise

    def test_invalid_url_exits_400(self):
        """E_VID_002_INVALID_URL → exit 400 + 3 段式输出。"""
        runner = CliRunner()
        exc = VideoIngestError(ErrorCode.E_VID_002_INVALID_URL.value, "坏 URL")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 400
        assert "场景" in result.output
        assert "原因" in result.output
        assert "建议" in result.output
        assert "E_VID_002_INVALID_URL" in result.output

    def test_tool_missing_exits_403(self):
        """E_PF_001_TOOL_MISSING → exit 403。"""
        runner = CliRunner()
        exc = VideoIngestError(ErrorCode.E_PF_001_TOOL_MISSING.value, "缺 yt-dlp")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 403

    def test_config_missing_exits_401(self):
        """E_CFG_001_CONFIG_MISSING → exit 401。"""
        runner = CliRunner()
        exc = VideoIngestError(ErrorCode.E_CFG_001_CONFIG_MISSING.value, "缺 API key")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 401

    def test_network_timeout_exits_500(self):
        """E_DL_001_NETWORK_TIMEOUT → exit 500。"""
        runner = CliRunner()
        exc = VideoIngestError(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value, "超时")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 500

    def test_video_not_found_exits_404(self):
        """E_VID_001_VIDEO_NOT_FOUND → exit 404。"""
        runner = CliRunner()
        exc = VideoIngestError(ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value, "视频没了")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 404

    def test_generic_research_error_exits_1(self):
        """非 VideoIngestError 的 ResearchToolError → exit 1（路径不变）。"""
        runner = CliRunner()
        exc = ResearchToolError("普通研究错误")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 1

    def test_unregistered_code_fallback_exits_1(self):
        """未注册错误码 → 降级通用路径（exit 1），处理器不崩溃。"""
        runner = CliRunner()
        exc = VideoIngestError("E_NOT_REGISTERED_999", "未知码")
        with patch(
            "research_tool.application.video_pipeline.process_videos",
            self._make_raise(exc),
        ):
            result = runner.invoke(app, ["run", "topic", "--video-url", self._BILI_URL])
        assert result.exit_code == 1
        assert "E_NOT_REGISTERED_999" in result.output
        # 降级路径不走 3 段式（无 "场景:"）
        assert "场景" not in result.output
