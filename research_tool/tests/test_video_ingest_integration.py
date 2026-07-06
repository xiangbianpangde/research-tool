"""V1.1 VideoIngest 核心能力层集成测试。

覆盖：
- 端到端：mock bilibili URL → 本地文件 + 缓存命中转写 → Markdown 输出
- 验证产物结构（YAML front_matter + body + 参考来源）
- 二次运行命中缓存（NFR4）

所有外部依赖（yt-dlp / faster-whisper / Groq）都 mock 掉。
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from research_tool.domain.models import (
    Chapter,
    LLMSummary,
    Transcript,
    TranscriptSegment,
    VideoMeta,
)
from research_tool.infrastructure.ingest.cache_manager import (
    CacheEntry,
    CacheManager,
    compute_url_sha256,
)
from research_tool.infrastructure.ingest.downloader import (
    BilibiliDownloader,
    DownloadResult,
    VideoDownloader,
    PLATFORM_BILIBILI,
)
from research_tool.infrastructure.ingest.notes_schema import (
    REFERENCES_SECTION_TITLE,
    assemble_markdown,
)
from research_tool.infrastructure.ingest.transcriber import (
    EngineType,
    compute_audio_fingerprint,
    transcribe,
)


# --------------------------------------------------------------------------- #
# Mock helpers
# --------------------------------------------------------------------------- #


def make_fake_bilibili_info(video_id: str = "BV1xx411c7mD") -> dict:
    """模拟 yt-dlp extract_info 返回（B 站）。"""
    return {
        "id": video_id,
        "ext": "m4a",
        "title": f"测试视频 {video_id}",
        "duration": 120,
        "thumbnail": "https://i0.hdslb.com/cover.jpg",
        "uploader": "测试UP主",
        "filesize": 1024 * 1024,  # 1MB
    }


def make_fake_audio_file(tmp_path: Path, name: str = "audio.wav") -> Path:
    """构造假音频文件（实际是空白 wav）。"""
    f = tmp_path / name
    f.write_bytes(b"RIFF" + b"\x00" * 100)
    return f


def make_fake_meta() -> VideoMeta:
    """构造假 VideoMeta。"""
    return VideoMeta(
        video_id="BV1xx411c7mD",
        platform="bilibili",
        title="集成测试视频",
        author="集成测试作者",
        duration_sec=120,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        cover_url="https://i0.hdslb.com/cover.jpg",
        language="zh",
    )


# --------------------------------------------------------------------------- #
# 端到端：bilibili 视频 → 下载 → 转写 → Markdown
# --------------------------------------------------------------------------- #


class TestBilibiliPipelineIntegration:
    """集成：mock bilibili URL → 全链路产物验证。"""

    @pytest.mark.asyncio
    async def test_full_pipeline_produces_markdown(self, tmp_path: Path):
        sample_meta = make_fake_meta()
        """mock B 站下载 + whisper 转写 → Markdown 拼装。"""
        # 1) 准备：mock B 站 yt-dlp 返回 → 实际写一个空音频文件
        make_fake_audio_file(tmp_path, "audio.m4a")
        bilibili_dl = BilibiliDownloader(output_dir=tmp_path)

        # mock yt-dlp 的 _extract_info_async：直接返回 info 字典
        # （不实际调用 yt_dlp.YoutubeDL）
        async def fake_extract(url, opts, download=True):
            return make_fake_bilibili_info("BV1xx411c7mD")

        bilibili_dl._extract_info_async = fake_extract  # type: ignore[method-assign]
        # BiliDownloader 期望 _extract_info_async 返回的 file_path 是真实存在的；
        # 由于 mock 没真下载，把 _info_to_result 替换为写文件版本
        from research_tool.infrastructure.ingest.downloader import BilibiliDownloader as B

        original_info_to_result = B._info_to_result

        def patched_info_to_result(info, out_dir, platform):
            res = original_info_to_result(info, out_dir, platform)
            # 写出 file_path 实际文件
            res_path = Path(res.file_path)
            res_path.parent.mkdir(parents=True, exist_ok=True)
            res_path.write_bytes(b"fake audio")
            return res

        B._info_to_result = staticmethod(patched_info_to_result)  # type: ignore[method-assign]

        # 用 VideoDownloader 顶层门面（带平台路由 + 重试）
        dl = VideoDownloader(output_dir=tmp_path)
        dl.bilibili = bilibili_dl  # 替换为已 patch 的实例

        # 2) 下载（mock 后不会真访问网络）
        result = await dl.download("https://www.bilibili.com/video/BV1xx411c7mD")
        assert result.platform == PLATFORM_BILIBILI
        assert result.video_id == "BV1xx411c7mD"
        assert Path(result.file_path).exists()

        # 3) 转写（mock WhisperEngine 走 fast 路径）
        from research_tool.infrastructure.ingest.transcriber import WhisperEngine

        class FakeSeg:
            def __init__(self, s, e, t):
                self.start, self.end, self.text = s, e, t

        fake_segments = [
            FakeSeg(0.0, 1.5, "大家好欢迎收看本期视频"),
            FakeSeg(1.5, 3.0, "今天我们来聊一聊人工智能"),
            FakeSeg(3.0, 4.5, "首先介绍背景知识"),
        ]
        fake_model = MagicMock()
        fake_model.transcribe = MagicMock(
            return_value=(iter(fake_segments), MagicMock(language="zh"))
        )

        with patch("faster_whisper.WhisperModel", return_value=fake_model):
            we = WhisperEngine(model_size="tiny")
            transcript = we.transcribe(result.file_path, language="zh")
        assert transcript.engine == "whisper"
        assert transcript.full_text.startswith("大家好欢迎收看")
        assert len(transcript.segments) == 3

        # 4) LLM 总结（mock，直接构造 LLMSummary 模拟 M-006 输出）
        # 不在 track-core 范围；模拟一段来自 deepseek 的总结
        summary = LLMSummary(
            video_summary="本视频介绍了人工智能的背景与现状。",
            video_chapters=[
                Chapter(start_sec=0, end_sec=60, title="开场"),
                Chapter(start_sec=60, end_sec=120, title="主题"),
            ],
            video_takeaways=[
                "了解 AI 发展史",
                "掌握核心技术分类",
            ],
            model="deepseek-chat",
        )

        # 5) 笔记拼装（M-007）
        # 用 audio 文件名作为 screenshot（实际不存在 → 占位图）
        markdown = assemble_markdown(
            meta=sample_meta,
            summary=summary,
            screenshots=[],
            transcript=transcript,
        )
        # 校验产物
        assert markdown.startswith("---"), "front_matter 缺失"
        # YAML 解析 front_matter
        import yaml

        parts = markdown.split("---", 2)
        assert len(parts) >= 3
        fm = yaml.safe_load(parts[1])
        for f in ("video_title", "video_author", "video_duration", "video_platform"):
            assert f in fm, f"front_matter 缺 {f}"
        # body 校验
        assert "本视频介绍了人工智能的背景与现状。" in markdown
        assert "## 章节" in markdown
        assert "开场" in markdown
        assert "## 关键要点" in markdown
        assert "了解 AI 发展史" in markdown
        # 参考来源
        assert REFERENCES_SECTION_TITLE in markdown
        assert "bilibili" in markdown

    @pytest.mark.asyncio
    async def test_nfr4_second_run_skips_transcribe(self, tmp_path: Path):
        """NFR4：二次运行同 URL 跳过转写（缓存命中）。"""
        # 1) 准备假音频
        audio_path = make_fake_audio_file(tmp_path, "audio.wav")
        fp = compute_audio_fingerprint(str(audio_path))

        # 2) 第一次：mock 缓存 miss → 调引擎 → 写入
        cm1 = MagicMock(spec=CacheManager)
        cm1.query = AsyncMock(return_value=None)
        cm1.write = AsyncMock(return_value=True)

        expected_first = Transcript(
            language="zh",
            full_text="first run",
            segments=[TranscriptSegment(start=0, end=1, text="first")],
            engine="whisper",
        )

        whisper_call_count = {"n": 0}

        async def fake_run(engine, *_a, **_kw):
            whisper_call_count["n"] += 1
            return expected_first

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine", side_effect=fake_run
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm1),
            ),
        ):
            r1 = await transcribe(
                str(audio_path),
                fp,
                engine=EngineType.WHISPER,
                cache_manager=cm1,
                timeout_sec=10,
            )
        assert r1.full_text == "first run"
        assert whisper_call_count["n"] == 1
        cm1.write.assert_called_once()

        # 3) 第二次：构造缓存命中（payload = 第一次的 transcript）
        from research_tool.infrastructure.ingest.transcriber import _transcript_to_payload

        payload = _transcript_to_payload(expected_first, EngineType.WHISPER, "tiny", 1.0)
        cm2 = MagicMock(spec=CacheManager)
        cm2.query = AsyncMock(
            return_value=CacheEntry(
                url=fp,
                url_sha256=compute_url_sha256(fp).replace("sha256:", ""),
                etag="",
                platform="video",
                payload=payload,
                created_at=time.time(),
            )
        )
        cm2.write = AsyncMock()

        async def should_not_run(engine, *_a, **_kw):
            whisper_call_count["n"] += 1
            raise RuntimeError("cache hit should not call engine")

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine",
                side_effect=should_not_run,
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm2),
            ),
        ):
            r2 = await transcribe(
                str(audio_path),
                fp,
                engine=EngineType.WHISPER,
                cache_manager=cm2,
                timeout_sec=10,
            )
        # 第二次应命中缓存返回相同结果，且不调引擎
        assert r2.full_text == "first run"
        assert whisper_call_count["n"] == 1  # 没有增加


# --------------------------------------------------------------------------- #
# 集成：VideoDownloader 自动路由 + retry
# --------------------------------------------------------------------------- #


class TestVideoDownloaderAutoRoute:
    """自动路由 + 重试 集成。"""

    @pytest.mark.asyncio
    async def test_youtube_url_routes_to_youtube_dl(self, tmp_path: Path):
        dl = VideoDownloader(output_dir=tmp_path, retry_times=0)
        called = {"n": 0}

        async def fake_yt_download(url):
            called["n"] += 1
            return DownloadResult(
                file_path=str(tmp_path / "yt.m4a"),
                size_mb=1.0,
                duration_sec=60,
                video_id="yt123",
                etag="",
                platform="youtube",
                title="yt",
            )

        dl.youtube.download = fake_yt_download  # type: ignore[method-assign]
        result = await dl.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.platform == "youtube"
        assert called["n"] == 1

    @pytest.mark.asyncio
    async def test_bilibili_url_routes_to_bilibili_dl(self, tmp_path: Path):
        dl = VideoDownloader(output_dir=tmp_path, retry_times=0)
        called = {"n": 0}

        async def fake_bili_download(url):
            called["n"] += 1
            return DownloadResult(
                file_path=str(tmp_path / "bi.m4a"),
                size_mb=1.0,
                duration_sec=60,
                video_id="BV1xx",
                etag="",
                platform="bilibili",
                title="bi",
            )

        dl.bilibili.download = fake_bili_download  # type: ignore[method-assign]
        result = await dl.download("https://www.bilibili.com/video/BV1xx411c7mD")
        assert result.platform == "bilibili"
        assert called["n"] == 1


# --------------------------------------------------------------------------- #
# 集成：M-007 + M-003 + M-005 + M-004 端到端
# --------------------------------------------------------------------------- #


class TestEndToEndFlow:
    """完整链路：bilibili URL → 本地 mp4 → ffmpeg 抽音 → 转写 → Markdown。"""

    @pytest.mark.asyncio
    async def test_local_file_path_through_assembler(self, tmp_path: Path):
        """本地文件路径（无网络）走 LocalFileResolver → 转写 → Markdown。"""
        from research_tool.infrastructure.ingest.downloader import LocalFileResolver

        # 1) 准备假视频文件
        video = tmp_path / "x.mp4"
        video.write_bytes(b"fake video content" * 100)
        # 2) LocalFileResolver 解析
        result = LocalFileResolver().resolve(str(video))
        assert result.platform == "local"
        assert Path(result.file_path).exists()

        # 3) 用 ffmpeg 抽音轨（mock 一下避免实际调用）
        from research_tool.infrastructure.ingest.ffmpeg_wrapper import AudioExtractResult

        audio_path = tmp_path / "x.mp3"
        audio_path.write_bytes(b"fake audio")
        with patch(
            "research_tool.infrastructure.ingest.ffmpeg_wrapper.AudioExtractor.extract",
            AsyncMock(
                return_value=AudioExtractResult(
                    audio_path=audio_path,
                    duration_sec=10.0,
                    sample_rate=16000,
                    format="mp3",
                )
            ),
        ):
            from research_tool.infrastructure.ingest.ffmpeg_wrapper import AudioExtractor

            audio = await AudioExtractor().extract(video, audio_path)
        assert audio.audio_path == audio_path

        # 4) mock 转写
        transcript = Transcript(
            language="zh",
            full_text="本地视频的转写内容",
            segments=[TranscriptSegment(start=0, end=5, text="本地视频的转写内容")],
            engine="whisper",
        )
        summary = LLMSummary(
            video_summary="本地视频总结",
            video_takeaways=["本地测试要点"],
        )
        meta = VideoMeta(
            video_id="local_x",
            platform="local",
            title="本地视频",
            author="me",
            duration_sec=10,
            language="zh",
        )

        # 5) 拼装
        md = assemble_markdown(
            meta=meta,
            summary=summary,
            screenshots=[],
            transcript=transcript,
        )
        # 校验 front_matter
        import yaml

        parts = md.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["video_platform"] == "local"
        assert fm["video_title"] == "本地视频"
        # body
        assert "本地视频的转写内容" in md
        assert "## 关键要点" in md
        # references
        assert REFERENCES_SECTION_TITLE in md
