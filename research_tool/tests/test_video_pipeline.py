"""V1.1 VideoIngest 端到端编排器 + URL 校验单元测试。

覆盖：
- URL 校验（bilibili 5 种形态 + youtube 4 种形态 + 白名单拒绝）
- VideoPipeline 编排（多 URL 校验 + 并发调度 + 5 阶段触发）
- _make_meta 数据转换
- 模块级 process_videos 便捷函数
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from research_tool.application.video_pipeline import (
    E_VID_URL_REJECTED,
    VideoPipeline,
    VideoPipelineReport,
    VideoProcessResult,
    _extract_bilibili_id,
    _extract_youtube_id,
    _make_meta,
    process_videos,
    validate_video_url,
)
from research_tool.domain.errors import VideoIngestError
from research_tool.domain.models import VideoURL


# --------------------------------------------------------------------------- #
# URL 校验
# --------------------------------------------------------------------------- #


class TestValidateVideoUrl:
    """URL 白名单校验测试。"""

    @pytest.mark.parametrize(
        "url,expected_platform,expected_id_part",
        [
            # B 站 BV
            ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili", "BV1xx411c7mD"),
            ("https://bilibili.com/video/BV1abc", "bilibili", "BV1abc"),
            # B 站 av
            ("https://www.bilibili.com/video/av12345", "bilibili", "av12345"),
            # b23.tv 短链
            ("https://b23.tv/abc123", "bilibili", ""),  # 短链不直接含 id
            # YouTube watch
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
            ("https://youtube.com/watch?v=abcDEF12345", "youtube", "abcDEF12345"),
            # youtu.be 短链
            ("https://youtu.be/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
            # YouTube shorts
            ("https://www.youtube.com/shorts/abc123", "youtube", "abc123"),
        ],
    )
    def test_valid_urls(self, url, expected_platform, expected_id_part):
        result = validate_video_url(url)
        assert isinstance(result, VideoURL)
        assert result.platform == expected_platform
        if expected_id_part:
            assert expected_id_part in (result.video_id or "")

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.douyin.com/video/12345",  # 抖音
            "https://v.kuaishou.com/abc",  # 快手
            "https://www.xiaoyuzhoufm.com/episode/abc",  # 小宇宙
            "https://example.com/video/123",  # 未知
            "not a url at all",  # 完全非法
            "",  # 空字符串
        ],
    )
    def test_invalid_urls_raise(self, url):
        if not url:
            # 空字符串 → ImportError 风格（应抛）
            with pytest.raises(VideoIngestError) as exc_info:
                validate_video_url(url)
            assert exc_info.value.code == E_VID_URL_REJECTED
        else:
            with pytest.raises(VideoIngestError) as exc_info:
                validate_video_url(url)
            assert exc_info.value.code == E_VID_URL_REJECTED

    def test_non_string_raises(self):
        with pytest.raises(VideoIngestError) as exc_info:
            validate_video_url(None)  # type: ignore[arg-type]
        assert exc_info.value.code == E_VID_URL_REJECTED

    def test_whitespace_stripped(self):
        result = validate_video_url("  https://www.bilibili.com/video/BV1xx  ")
        assert result.platform == "bilibili"


# --------------------------------------------------------------------------- #
# URL ID 提取
# --------------------------------------------------------------------------- #


class TestExtractHelpers:
    """内部 ID 提取函数测试。"""

    def test_bilibili_bv(self):
        assert _extract_bilibili_id("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"

    def test_bilibili_av(self):
        assert _extract_bilibili_id("https://www.bilibili.com/video/av12345") == "av12345"

    def test_bilibili_short_link(self):
        # b23.tv 短链不含 BV/av
        assert _extract_bilibili_id("https://b23.tv/abc123") == ""

    def test_youtube_watch(self):
        assert _extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtube_short_url(self):
        assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtube_shorts(self):
        assert _extract_youtube_id("https://www.youtube.com/shorts/abc123") == "abc123"

    def test_youtube_no_id(self):
        assert _extract_youtube_id("https://www.youtube.com/") == ""


# --------------------------------------------------------------------------- #
# _make_meta 数据转换
# --------------------------------------------------------------------------- #


class TestMakeMeta:
    """download_result → VideoMeta 测试。"""

    def test_make_meta_basic(self, tmp_path: Path):
        from research_tool.infrastructure.ingest.downloader import DownloadResult

        fake_audio = tmp_path / "test.m4a"
        fake_audio.write_bytes(b"fake")
        dr = DownloadResult(
            file_path=str(fake_audio),
            size_mb=1.5,
            duration_sec=120,
            video_id="BV1xx",
            etag="",
            platform="bilibili",
            title="测试视频",
            cover_url="https://i0.hdslb.com/cover.jpg",
        )
        url = "https://www.bilibili.com/video/BV1xx"
        video_url = VideoURL(platform="bilibili", url=url, video_id="BV1xx")
        meta = _make_meta(dr, video_url)
        assert meta.video_id == "BV1xx"
        assert meta.title == "测试视频"
        assert meta.duration_sec == 120
        assert meta.url == video_url.url
        assert meta.cover_url == "https://i0.hdslb.com/cover.jpg"
        assert meta.platform == "bilibili"

    def test_make_meta_no_id_uses_video_id(self, tmp_path: Path):
        from research_tool.infrastructure.ingest.downloader import DownloadResult

        fake_audio = tmp_path / "test.m4a"
        fake_audio.write_bytes(b"fake")
        dr = DownloadResult(
            file_path=str(fake_audio),
            size_mb=0.0,
            duration_sec=0,
            video_id="BV1xx",
            etag="",
            platform="bilibili",
            title="",
        )
        url = "https://www.bilibili.com/video/BV1xx"
        video_url = VideoURL(platform="bilibili", url=url, video_id=None)
        meta = _make_meta(dr, video_url)
        # 应回退到 download_result.video_id
        assert meta.video_id == "BV1xx"

    def test_make_meta_empty_title_uses_default(self, tmp_path: Path):
        from research_tool.infrastructure.ingest.downloader import DownloadResult

        fake_audio = tmp_path / "test.m4a"
        fake_audio.write_bytes(b"fake")
        dr = DownloadResult(
            file_path=str(fake_audio),
            size_mb=0.0,
            duration_sec=0,
            video_id="abc",
            etag="",
            platform="youtube",
            title="",
        )
        video_url = VideoURL(platform="youtube", url="https://youtu.be/abc", video_id="abc")
        meta = _make_meta(dr, video_url)
        assert meta.title == "未命名视频"


# --------------------------------------------------------------------------- #
# VideoPipeline 顶层编排
# --------------------------------------------------------------------------- #


class TestVideoPipeline:
    """VideoPipeline 集成测试（mock downloader/transcriber/summarizer）。"""

    @pytest.mark.asyncio
    async def test_process_urls_all_valid(self, tmp_path: Path):
        """所有 URL 有效 + mock 任务函数 → 全部成功。"""
        pipeline = VideoPipeline(topic="test", work_dir=tmp_path)

        async def fake_task(url: str) -> Path:
            # 模拟 write_markdown 行为
            out = tmp_path / f"test_{url[-5:]}.md"
            out.write_text(f"# {url}\n", encoding="utf-8")
            return out

        urls = [
            "https://www.bilibili.com/video/BV1xx1",
            "https://www.bilibili.com/video/BV1xx2",
        ]
        report = await pipeline.process_urls(urls, task_func=fake_task)

        assert report.topic == "test"
        assert len(report.results) == 2
        assert all(r.status == "success" for r in report.results)
        assert report.success_count == 2
        assert report.failed_count == 0

    @pytest.mark.asyncio
    async def test_process_urls_some_invalid(self, tmp_path: Path):
        """部分 URL 非法 → 非法立即记为 failed。"""
        pipeline = VideoPipeline(topic="test", work_dir=tmp_path)

        async def fake_task(url: str) -> Path:
            out = tmp_path / "good.md"
            out.write_text("# good\n", encoding="utf-8")
            return out

        urls = [
            "https://www.bilibili.com/video/BV1xx",  # 合法
            "https://www.douyin.com/video/123",  # 非法
            "https://www.youtube.com/watch?v=abc123",  # 合法
        ]
        report = await pipeline.process_urls(urls, task_func=fake_task)

        assert len(report.results) == 3
        assert report.results[0].status == "success"
        assert report.results[1].status == "failed"
        assert E_VID_URL_REJECTED in (report.results[1].error or "")
        assert report.results[2].status == "success"

    @pytest.mark.asyncio
    async def test_process_urls_task_failure_isolated(self, tmp_path: Path):
        """任务执行失败 → 该 URL 记为 failed，不影响其他。"""
        pipeline = VideoPipeline(topic="test", work_dir=tmp_path)

        async def flaky_task(url: str) -> Path:
            if "BV1" in url:
                raise RuntimeError("simulated transcribe failure")
            out = tmp_path / "good.md"
            out.write_text("# good\n", encoding="utf-8")
            return out

        urls = [
            "https://www.bilibili.com/video/BV1fail",  # 任务失败
            "https://www.youtube.com/watch?v=good",  # 成功
        ]
        report = await pipeline.process_urls(urls, task_func=flaky_task)

        assert report.results[0].status == "failed"
        assert "transcribe failure" in (report.results[0].error or "")
        assert report.results[1].status == "success"

    @pytest.mark.asyncio
    async def test_process_urls_runs_pipeline(self, tmp_path: Path):
        """run_pipeline=True 时，至少 1 个成功 → 触发 5 阶段管道。"""
        pipeline = VideoPipeline(topic="test", work_dir=tmp_path, run_pipeline=True)

        async def fake_task(url: str) -> Path:
            out = tmp_path / "test.md"
            out.write_text("# good\n", encoding="utf-8")
            return out

        # Mock trigger_pipeline（patch 源模块，因为 import 在函数内）
        mock_stages = MagicMock()
        mock_stages.success = True
        mock_stages.stages_run = ["clean", "extract"]
        mock_stages.duration_ms = 100

        with patch(
            "research_tool.infrastructure.ingest.pipeline_adapter.trigger_pipeline",
            AsyncMock(return_value=mock_stages),
        ) as mock_trigger:
            report = await pipeline.process_urls(
                ["https://www.bilibili.com/video/BV1xx"],
                task_func=fake_task,
            )
        assert report.stages_result is mock_stages
        mock_trigger.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_urls_no_pipeline_if_all_failed(self, tmp_path: Path):
        """全部失败时不触发管道。"""
        pipeline = VideoPipeline(topic="test", work_dir=tmp_path, run_pipeline=True)

        with patch(
            "research_tool.infrastructure.ingest.pipeline_adapter.trigger_pipeline",
            AsyncMock(return_value=MagicMock(success=False, stages_run=[], duration_ms=0)),
        ) as mock_trigger:
            report = await pipeline.process_urls(
                ["https://www.douyin.com/video/123"],  # 非法
            )
        assert report.stages_result is None
        mock_trigger.assert_not_called()


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


class TestModuleEntry:
    """process_videos 便捷函数测试。"""

    @pytest.mark.asyncio
    async def test_process_videos_basic(self, tmp_path: Path):
        # process_videos 不接受 task_func（生产环境始终走真实任务）
        # 这里通过 VideoPipeline 间接覆盖
        from research_tool.application.video_pipeline import VideoPipeline

        async def fake_task(url: str) -> Path:
            out = tmp_path / "test.md"
            out.write_text("# good\n", encoding="utf-8")
            return out

        pipeline = VideoPipeline(topic="t", work_dir=tmp_path, run_pipeline=False)
        report = await pipeline.process_urls(
            urls=["https://www.bilibili.com/video/BV1xx"],
            task_func=fake_task,
        )
        assert isinstance(report, VideoPipelineReport)
        assert report.topic == "t"
        assert report.success_count == 1

    @pytest.mark.asyncio
    async def test_process_videos_module_level(self, tmp_path: Path):
        """模块级 process_videos 也可用（用真实任务但有 URL 校验短路）。"""
        # 全部 URL 非法 → 全部失败 → 不抛错（异常隔离）
        report = await process_videos(
            topic="t",
            urls=["https://www.douyin.com/video/123"],
            work_dir=tmp_path,
            run_pipeline=False,
        )
        assert report.failed_count == 1
        assert report.success_count == 0


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


class TestVideoProcessResult:
    """结果数据类测试。"""

    def test_default_status_success(self):
        r = VideoProcessResult(url="u")
        assert r.status == "success"

    def test_fields(self, tmp_path: Path):
        p = tmp_path / "test.md"
        r = VideoProcessResult(
            url="u",
            platform="bilibili",
            video_id="BV1",
            status="failed",
            markdown_path=p,
            error="boom",
            duration_ms=500,
        )
        assert r.platform == "bilibili"
        assert r.video_id == "BV1"
        assert r.markdown_path == p
        assert r.error == "boom"
        assert r.duration_ms == 500

    def test_frozen(self):
        r = VideoProcessResult(url="u")
        with pytest.raises(Exception):  # FrozenInstanceError
            r.status = "failed"  # type: ignore[misc]


class TestVideoPipelineReport:
    """报告数据类测试。"""

    def test_success_failed_counts(self):
        r = VideoPipelineReport(
            topic="t",
            results=[
                VideoProcessResult(url="a", status="success"),
                VideoProcessResult(url="b", status="failed"),
                VideoProcessResult(url="c", status="success"),
            ],
        )
        assert r.success_count == 2
        assert r.failed_count == 1

    def test_default_empty(self):
        r = VideoPipelineReport(topic="t")
        assert r.results == []
        assert r.success_count == 0
        assert r.failed_count == 0
        assert r.stages_result is None
        assert r.total_duration_ms == 0
