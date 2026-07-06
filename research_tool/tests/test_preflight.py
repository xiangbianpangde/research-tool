"""M-002 预检模块单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from research_tool.infrastructure.ingest.preflight import (
    DEFAULT_TTL_SECONDS,
    PreflightFacade,
    PreflightReport,
    check_all,
    invalidate_cache,
)


class TestPreflightReport:
    """PreflightReport 数据类。"""

    def test_construction(self):
        r = PreflightReport(
            ytdlp_ok=True,
            ytdlp_version="2024.5.1",
            ffmpeg_ok=True,
            ffmpeg_version="6.0",
            whisper_ok=False,
            whisper_model_size=None,
            cache_writable=True,
            cache_dir="/tmp/cache",
            timestamp="2026-06-01T12:00:00Z",
            ttl_seconds=60,
        )
        assert r.ytdlp_ok is True
        assert r.ffmpeg_version == "6.0"

    def test_is_blocking_only_ytdlp_matters(self):
        r = PreflightReport(
            ytdlp_ok=False,
            ytdlp_version=None,
            ffmpeg_ok=True,
            ffmpeg_version="6.0",
            whisper_ok=True,
            whisper_model_size="medium",
            cache_writable=True,
            cache_dir="/tmp/c",
            timestamp="",
            ttl_seconds=60,
        )
        assert r.is_blocking() is True

        r2 = PreflightReport(
            ytdlp_ok=True,
            ytdlp_version="2024",
            ffmpeg_ok=False,
            ffmpeg_version=None,
            whisper_ok=False,
            whisper_model_size=None,
            cache_writable=True,
            cache_dir="/tmp/c",
            timestamp="",
            ttl_seconds=60,
        )
        # ffmpeg/whisper 缺失不阻塞（FAIL_SOFT）
        assert r2.is_blocking() is False

    def test_to_dict_roundtrip(self):
        r = PreflightReport(
            ytdlp_ok=True,
            ytdlp_version="2024",
            ffmpeg_ok=True,
            ffmpeg_version="6.0",
            whisper_ok=True,
            whisper_model_size="medium",
            cache_writable=True,
            cache_dir="/tmp/c",
            timestamp="2026-06-01T12:00:00Z",
            ttl_seconds=60,
        )
        d = r.to_dict()
        r2 = PreflightReport.from_dict(d)
        assert r == r2


class TestPreflightFacade:
    """PreflightFacade 异步并行检测。"""

    @pytest.mark.asyncio
    async def test_check_all_happy_path(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(True, "2024.5.1"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "6.0"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path)
            report = await facade.check_all()
        assert report.ytdlp_ok is True
        assert report.ytdlp_version == "2024.5.1"
        assert report.ffmpeg_version == "6.0"
        assert report.whisper_model_size == "medium"
        assert report.cache_writable is True

    @pytest.mark.asyncio
    async def test_check_all_ytdlp_missing_is_blocking(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(False, None),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "6.0"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path)
            report = await facade.check_all()
        assert report.ytdlp_ok is False
        assert facade.is_blocking(report) is True

    @pytest.mark.asyncio
    async def test_check_all_soft_failures_not_blocking(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(True, "2024"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(False, None),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(False, None),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=False,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path)
            report = await facade.check_all()
        assert report.ytdlp_ok is True
        assert report.ffmpeg_ok is False
        assert report.whisper_ok is False
        assert report.cache_writable is False
        # 只有 ytdlp 缺失才阻塞
        assert facade.is_blocking(report) is False

    @pytest.mark.asyncio
    async def test_check_all_caches_result(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(True, "2024"),
            ) as mock_yt,
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "6.0"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path, ttl_seconds=60)
            r1 = await facade.check_all()
            # 第二次：应命中缓存，不调 checker
            r2 = await facade.check_all()
        assert r1 == r2
        # _check_ytdlp_sync 只应在第一次被调用
        assert mock_yt.call_count == 1

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(True, "2024"),
            ) as mock_yt,
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "6.0"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path)
            await facade.check_all()
            await facade.check_all(force_refresh=True)
        assert mock_yt.call_count == 2

    @pytest.mark.asyncio
    async def test_check_handles_checker_exceptions(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "6.0"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            facade = PreflightFacade(cache_dir=tmp_path)
            report = await facade.check_all()
        # ytdlp checker 异常 → 视为失败（不抛错，降级）
        assert report.ytdlp_ok is False


class TestModuleLevel:
    """模块级便捷函数。"""

    @pytest.mark.asyncio
    async def test_check_all(self, tmp_path: Path):
        invalidate_cache()
        with (
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ytdlp_sync",
                return_value=(True, "v"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_ffmpeg_sync",
                return_value=(True, "v"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_whisper_sync",
                return_value=(True, "medium"),
            ),
            patch(
                "research_tool.infrastructure.ingest.preflight._check_cache_writable_sync",
                return_value=True,
            ),
        ):
            report = await check_all(cache_dir=tmp_path)
        assert report.ytdlp_ok is True
        assert report.ttl_seconds == DEFAULT_TTL_SECONDS

    def test_invalidate_cache(self):
        # 不抛错即通过
        invalidate_cache()
