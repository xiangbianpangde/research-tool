"""M-003 下载器单元测试。

覆盖：
- URL 平台识别
- Cookie 0o600 权限校验
- 本地文件解析
- 重试逻辑（403 不重试）
- 视频 ID 提取
"""

from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research_tool.domain.errors import DownloadError
from research_tool.infrastructure.ingest.downloader import (
    COOKIE_REQUIRED_PERMS,
    E_DL_001,
    E_DL_002_VERSION_TOO_OLD,
    E_DL_003_NETWORK,
    E_DL_BILI_403,
    E_DL_LOCAL_001,
    E_DL_LOCAL_002,
    BilibiliDownloader,
    CookieInjector,
    LocalFileResolver,
    PLATFORM_BILIBILI,
    PLATFORM_LOCAL,
    PLATFORM_YOUTUBE,
    VideoDownloader,
    YtDlpVersionValidator,
    detect_platform,
    extract_video_id,
    inject_cookie,
    resolve_local,
)


# --------------------------------------------------------------------------- #
# detect_platform / extract_video_id
# --------------------------------------------------------------------------- #


class TestDetectPlatform:
    """URL 平台自动识别。"""

    def test_youtube_watch_url(self):
        assert detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == PLATFORM_YOUTUBE
        assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == PLATFORM_YOUTUBE
        assert detect_platform("https://m.youtube.com/watch?v=abc") == PLATFORM_YOUTUBE

    def test_youtube_shorts(self):
        assert detect_platform("https://www.youtube.com/shorts/abc123") == PLATFORM_YOUTUBE

    def test_bilibili_bv(self):
        assert detect_platform("https://www.bilibili.com/video/BV1xx411c7mD") == PLATFORM_BILIBILI

    def test_bilibili_av(self):
        assert detect_platform("https://www.bilibili.com/video/av12345678") == PLATFORM_BILIBILI

    def test_local_path(self):
        assert detect_platform("C:/Users/test/video.mp4") == PLATFORM_LOCAL
        assert detect_platform("/home/user/video.webm") == PLATFORM_LOCAL
        assert detect_platform("./relative.mp4") == PLATFORM_LOCAL

    def test_unknown_url(self):
        assert detect_platform("https://example.com/foo") == ""

    def test_empty_string(self):
        assert detect_platform("") == ""


class TestExtractVideoId:
    """视频 ID 提取。"""

    def test_bilibili_bv(self):
        assert (
            extract_video_id("https://www.bilibili.com/video/BV1xx411c7mD", PLATFORM_BILIBILI)
            == "BV1xx411c7mD"
        )

    def test_bilibili_av(self):
        assert (
            extract_video_id("https://www.bilibili.com/video/av12345", PLATFORM_BILIBILI)
            == "av12345"
        )

    def test_youtube_11char(self):
        assert (
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ", PLATFORM_YOUTUBE)
            == "dQw4w9WgXcQ"
        )

    def test_youtube_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ", PLATFORM_YOUTUBE) == "dQw4w9WgXcQ"

    def test_youtube_shorts(self):
        assert (
            extract_video_id("https://www.youtube.com/shorts/abcDEF12345", PLATFORM_YOUTUBE)
            == "abcDEF12345"
        )

    def test_local_uses_filename(self):
        assert extract_video_id("/path/to/myvideo.mp4", PLATFORM_LOCAL) == "myvideo"

    def test_unknown_returns_empty(self):
        assert extract_video_id("https://x.com/v", "unknown") == ""


# --------------------------------------------------------------------------- #
# CookieInjector
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX 权限检查在 Windows 上跳过")
class TestCookieInjectorPosix:
    """CookieInjector 在 Unix 上的 0o600 权限校验。"""

    def test_valid_0o600(self, tmp_path: Path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# Netscape\n")
        os.chmod(cookie, COOKIE_REQUIRED_PERMS)
        injector = CookieInjector(cookie)
        assert injector.validate_perms() is True

    def test_wrong_perms_raises(self, tmp_path: Path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# Netscape\n")
        os.chmod(cookie, 0o644)  # 群组可读，不安全
        injector = CookieInjector(cookie)
        with pytest.raises(DownloadError) as exc_info:
            injector.validate_perms()
        assert exc_info.value.code == E_DL_001

    def test_missing_file_raises(self, tmp_path: Path):
        injector = CookieInjector(tmp_path / "missing.txt")
        with pytest.raises(DownloadError) as exc_info:
            injector.validate_perms()
        assert exc_info.value.code == E_DL_001

    def test_inject_args_appends_cookies(self, tmp_path: Path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# Netscape\n")
        os.chmod(cookie, COOKIE_REQUIRED_PERMS)
        args = ["yt-dlp", "--format", "best"]
        out = CookieInjector(cookie).inject_args(args)
        assert "--cookies" in out
        assert str(cookie) in out
        assert out[:3] == ["yt-dlp", "--format", "best"]


class TestCookieInjectorWindows:
    """CookieInjector 在 Windows 上跳过 0o600 硬检查（ACL 限制）。"""

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only")
    def test_existing_file_passes(self, tmp_path: Path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# Netscape\n")
        # 不调 chmod → Windows 上 ACL 默认
        injector = CookieInjector(cookie)
        assert injector.validate_perms() is True

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only")
    def test_missing_still_raises(self, tmp_path: Path):
        injector = CookieInjector(tmp_path / "nope.txt")
        with pytest.raises(DownloadError):
            injector.validate_perms()


class TestModuleLevelInjectCookie:
    """模块级便捷函数 inject_cookie。"""

    def test_inject(self, tmp_path: Path):
        if platform.system() != "Windows":
            cookie = tmp_path / "cookies.txt"
            cookie.write_text("# Netscape\n")
            os.chmod(cookie, COOKIE_REQUIRED_PERMS)
            out = inject_cookie(["yt-dlp", "URL"], str(cookie))
            assert "--cookies" in out


# --------------------------------------------------------------------------- #
# LocalFileResolver
# --------------------------------------------------------------------------- #


class TestLocalFileResolver:
    """本地文件解析器。"""

    def test_resolve_mp4(self, tmp_path: Path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake mp4 content" * 100_000)  # ~1.5MB
        res = LocalFileResolver().resolve(str(f))
        assert res.platform == PLATFORM_LOCAL
        assert res.video_id == "video"
        assert res.size_mb > 0
        assert Path(res.file_path).exists()

    def test_resolve_webm(self, tmp_path: Path):
        f = tmp_path / "movie.webm"
        f.write_bytes(b"x" * 100)
        res = LocalFileResolver().resolve(str(f))
        assert res.platform == PLATFORM_LOCAL

    def test_validate_missing_raises(self, tmp_path: Path):
        with pytest.raises(DownloadError) as exc_info:
            LocalFileResolver().validate(str(tmp_path / "nope.mp4"))
        assert exc_info.value.code == E_DL_LOCAL_001

    def test_validate_unsupported_ext_raises(self, tmp_path: Path):
        f = tmp_path / "weird.xyz"
        f.write_bytes(b"x")
        with pytest.raises(DownloadError) as exc_info:
            LocalFileResolver().validate(str(f))
        assert exc_info.value.code == E_DL_LOCAL_002

    def test_validate_directory_raises(self, tmp_path: Path):
        with pytest.raises(DownloadError):
            LocalFileResolver().validate(str(tmp_path))

    def test_module_level_resolve_local(self, tmp_path: Path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"x")
        res = asyncio_run(resolve_local(str(f)))
        assert res.video_id == "x"


# --------------------------------------------------------------------------- #
# YtDlpVersionValidator
# --------------------------------------------------------------------------- #


class TestYtDlpVersionValidator:
    """yt-dlp 版本校验器（mock yt_dlp 模块）。"""

    def test_compare_true_when_newer(self):
        v = YtDlpVersionValidator(min_version="2023.07.06")
        assert v.compare("2024.12.13") is True

    def test_compare_true_when_equal(self):
        v = YtDlpVersionValidator(min_version="2023.07.06")
        assert v.compare("2023.07.06") is True

    def test_compare_false_when_older(self):
        v = YtDlpVersionValidator(min_version="2023.07.06")
        assert v.compare("2022.01.01") is False

    def test_check_raises_when_missing(self):
        """mock ImportError → 抛 DownloadError(E_DL_003_NETWORK)。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yt_dlp" or name.startswith("yt_dlp"):
                raise ImportError("No module named 'yt_dlp'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            v = YtDlpVersionValidator()
            with pytest.raises(DownloadError) as exc_info:
                v.check()
            assert exc_info.value.code == E_DL_003_NETWORK

    def test_validate_raises_when_too_old(self):
        """mock yt_dlp.version.__version__ 旧值。"""
        fake_yt_dlp = MagicMock()
        fake_yt_dlp.version = MagicMock()
        fake_yt_dlp.version.__version__ = "2022.01.01"
        fake_yt_dlp.version.version_tuple = (2022, 1, 1)
        with patch.dict(
            "sys.modules", {"yt_dlp": fake_yt_dlp, "yt_dlp.version": fake_yt_dlp.version}
        ):
            v = YtDlpVersionValidator(min_version="2023.07.06")
            with pytest.raises(DownloadError) as exc_info:
                v.validate()
            assert exc_info.value.code == E_DL_002_VERSION_TOO_OLD


# --------------------------------------------------------------------------- #
# VideoDownloader 重试 / 平台路由
# --------------------------------------------------------------------------- #


class TestVideoDownloaderRouting:
    """VideoDownloader 路由 + 重试。"""

    def test_build_video_url_youtube(self):
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        v = dl.build_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert v.platform == PLATFORM_YOUTUBE
        assert v.video_id == "dQw4w9WgXcQ"

    def test_build_video_url_bilibili(self):
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        v = dl.build_video_url("https://www.bilibili.com/video/BV1xx411c7mD")
        assert v.platform == PLATFORM_BILIBILI
        assert v.video_id == "BV1xx411c7mD"

    def test_build_video_url_local(self):
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        v = dl.build_video_url("C:/Users/test/video.mp4")
        assert v.platform == PLATFORM_LOCAL

    def test_build_video_url_unknown_raises(self):
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        with pytest.raises(DownloadError) as exc_info:
            dl.build_video_url("https://example.com/foo")
        assert exc_info.value.code == E_DL_001

    @pytest.mark.asyncio
    async def test_youtube_download_retries_then_succeeds(self):
        """首次失败 → 重试 1 次成功。"""
        dl = VideoDownloader(output_dir=tempfile.gettempdir(), retry_times=1)
        call_count = {"n": 0}

        async def fake_download(url):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise DownloadError(E_DL_003_NETWORK, "网络抖动")
            return _make_result("BV1xx", "ok")

        dl.youtube.download = fake_download  # type: ignore[method-assign]
        result = await dl.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result.video_id == "BV1xx"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_youtube_download_403_no_retry(self):
        """403 严格不重试（AR 调研 S-101）。"""
        dl = VideoDownloader(output_dir=tempfile.gettempdir(), retry_times=3)
        call_count = {"n": 0}

        async def fake_download(url):
            call_count["n"] += 1
            raise DownloadError(E_DL_BILI_403, "B 站 403")

        dl.youtube.download = fake_download  # type: ignore[method-assign]
        # 实际走 bilibili 路径（403 在 bilibili download 里）
        dl.bilibili.download = fake_download  # type: ignore[method-assign]
        with pytest.raises(DownloadError) as exc_info:
            await dl.download("https://www.bilibili.com/video/BV1xx")
        assert exc_info.value.code == E_DL_BILI_403
        assert call_count["n"] == 1  # 只调用 1 次

    @pytest.mark.asyncio
    async def test_local_download_routes_correctly(self, tmp_path: Path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"x" * 100)
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        result = await dl.download(str(f))
        assert result.platform == PLATFORM_LOCAL
        assert result.video_id == "x"

    @pytest.mark.asyncio
    async def test_download_returns_to_task(self, tmp_path: Path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"x")
        dl = VideoDownloader(output_dir=tempfile.gettempdir())
        task = await dl.download_to_task(str(f))
        # DownloadTask Pydantic model
        assert hasattr(task, "file_path")
        assert task.platform == PLATFORM_LOCAL


# --------------------------------------------------------------------------- #
# BilibiliDownloader._is_403
# --------------------------------------------------------------------------- #


class TestBilibiliIs403:
    """B 站 403 解析（仅 stderr 文本判定）。"""

    def test_detect_403(self):
        assert BilibiliDownloader._is_403("HTTP Error 403: Forbidden") is True

    def test_no_403(self):
        assert BilibiliDownloader._is_403("Some other error") is False

    def test_empty(self):
        assert BilibiliDownloader._is_403("") is False


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _make_result(video_id: str, title: str):
    """构造 DownloadResult（用于 mock 返回）。"""
    import tempfile

    from research_tool.infrastructure.ingest.downloader import DownloadResult

    return DownloadResult(
        file_path=f"{tempfile.gettempdir()}/{video_id}.m4a",
        size_mb=1.0,
        duration_sec=10,
        video_id=video_id,
        etag="",
        platform="youtube",
        title=title,
    )


def asyncio_run(coro):
    """把 coroutine 跑完（用于同步测试）。"""
    import asyncio

    return asyncio.run(coro)
