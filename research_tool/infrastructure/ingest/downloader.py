"""M-003 下载器（V1.1 VideoIngest）。

设计依据：
- [DD-001:M-003 下载器] yt-dlp 包装，支持 YouTube + Bilibili + 本地
- [DD-001:IC-005/007/008] 接口契约
- [调研: BiliNote backend/app/downloaders/{youtube,bilibili,local}_downloader.py]
  —— 移植 yt-dlp opts 构造模式，但简化为无 DB / 无截图 / 无 Web 依赖

职责：
- YouTube / Bilibili 视频下载（依赖 yt-dlp，NFR1 走 [video] extra）
- 本地文件解析（mp4/webm/mkv）
- Cookie 注入（B 站 403 时由用户配置，0o600 权限硬约束）
- yt-dlp 版本校验（启动期一次性）

异步：所有阻塞 I/O 用 asyncio.to_thread 包装（NFR 禁止同步阻塞）。
错误：所有失败通过 M-010 错误码登记 + 抛 DownloadError。
日志：M-011 emit_log（自动 sha256 替换 URL 敏感字段）。
缓存：M-004 命中时短路返回（IC-009），不实际跑 yt-dlp。
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ...common.logging_config import emit_log, get_logger
from ...domain.errors import (
    DownloadError,
    ErrorCode,
    register_error,
)
from ...domain.models import DownloadTask, VideoURL
from .cache_manager import CacheManager

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 模块级常量
# --------------------------------------------------------------------------- #


YTDLP_MIN_VERSION: str = "2023.07.06"

PLATFORM_YOUTUBE: str = "youtube"
PLATFORM_BILIBILI: str = "bilibili"
PLATFORM_LOCAL: str = "local"

LOCAL_SUPPORTED_EXTS: tuple[str, ...] = (".mp4", ".webm", ".mkv", ".m4a", ".mp3", ".wav")
COOKIE_REQUIRED_PERMS: int = 0o600
# V1.1：默认想拉一份"低画质视频+最佳音频"——视频流给关键帧截图用，音频给转写。
# 旧设置（仅音频）截不出帧，断了图文笔记里"图"的来源。可用 VIDEO_FORMAT 环境变量覆盖。
DEFAULT_YT_DLP_FORMAT: str = os.environ.get(
    "VIDEO_FORMAT",
    "bestvideo[height<=480]+bestaudio/best[height<=480]/bestaudio[ext=m4a]/bestaudio/best",
)


def _inject_ffmpeg_location(opts: dict[str, Any]) -> None:
    """给 yt-dlp opts 注入 ffmpeg_location（用 FFMPEG_PATH 环境变量），
    解决 Claude/CI 子进程 PATH 没刷新看不到 ffmpeg 的问题。"""
    ff = os.environ.get("FFMPEG_PATH")
    if ff and "ffmpeg_location" not in opts:
        # yt-dlp 接受 ffmpeg 二进制路径或其所在目录；用目录更通用（同时找 ffprobe）。
        opts["ffmpeg_location"] = str(Path(ff).parent) if Path(ff).suffix else ff


DEFAULT_OUTPUT_TEMPLATE: str = "%(id)s.%(ext)s"
DEFAULT_DOWNLOAD_TIMEOUT_SEC: int = 600  # 10 分钟

# 平台 → 平台首页域名（用于 Referer 头）
PLATFORM_REFERER: dict[str, str] = {
    PLATFORM_BILIBILI: "https://www.bilibili.com",
    PLATFORM_YOUTUBE: "https://www.youtube.com",
}

# 平台 URL 正则
_BILIBILI_URL_RE = re.compile(
    r"^(https?://)?(www\.)?bilibili\.com/video/(BV[a-zA-Z0-9]+|av\d+)"
    r"|^(https?://)?b23\.tv/\w+"
    r"|^(https?://)?(www\.)?bilibili\.com/video/(ss|sb)\d+",
    re.IGNORECASE,
)
_YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[\w-]+",
    re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(r"^[A-Za-z]:[\\\\/]|^/|^\./|^\.\./")


# --------------------------------------------------------------------------- #
# 错误码（与 M-010 ErrorCode 体系并行；本模块内部字符串常量）
# --------------------------------------------------------------------------- #


E_DL_001: str = ErrorCode.E_VID_002_INVALID_URL.value  # URL 非法
E_DL_002_VERSION_TOO_OLD: str = "E_DL_002_VERSION_TOO_OLD"  # yt-dlp 版本过旧
E_DL_003_NETWORK: str = ErrorCode.E_DL_001_NETWORK_TIMEOUT.value
E_DL_004_YT_DLP_FAILED: str = ErrorCode.E_DL_002_YT_DLP_FAILED.value
E_DL_BILI_403: str = "E_DL_BILI_403"
E_DL_LOCAL_001: str = "E_DL_LOCAL_001"
E_DL_LOCAL_002: str = "E_DL_LOCAL_002"


# --------------------------------------------------------------------------- #
# 异常类型
# --------------------------------------------------------------------------- #


# 注意：DownloadError 已在 src/domain/errors.py 定义（V1.1），
# 这里不复定义；本模块直接 import 使用。


# --------------------------------------------------------------------------- #
# 数据类（替代 BiliNote 的 AudioDownloadResult）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DownloadResult:
    """下载结果。

    与 DownloadTask（Pydantic 模型）互转；DownloadResult 是内部 dataclass
    用于 yt-dlp 解析流水，结束后通过 to_download_task() 转为 Pydantic 模型
    暴露给上层。
    """

    file_path: str
    size_mb: float
    duration_sec: int
    video_id: str
    etag: str
    platform: str
    title: str
    cover_url: str | None = None
    raw_info: dict[str, Any] | None = None

    def to_download_task(self) -> DownloadTask:
        """转 Pydantic 模型（DE-005）。"""
        return DownloadTask(
            file_path=self.file_path,
            size_mb=self.size_mb,
            duration_sec=self.duration_sec,
            video_id=self.video_id,
            etag=self.etag,
            platform=self.platform,
            title=self.title,
            cover_url=self.cover_url,
        )


# --------------------------------------------------------------------------- #
# URL 解析（替代 BiliNote extract_video_id）
# --------------------------------------------------------------------------- #


def detect_platform(url: str) -> str:
    """根据 URL 字符串判断平台。"""
    if not url:
        return ""
    # 本地路径
    if _LOCAL_PATH_RE.match(url) or os.path.isabs(url) or url.startswith("file://"):
        return PLATFORM_LOCAL
    if _BILIBILI_URL_RE.match(url):
        return PLATFORM_BILIBILI
    if _YOUTUBE_URL_RE.match(url):
        return PLATFORM_YOUTUBE
    return ""


def extract_video_id(url: str, platform: str) -> str:
    """从 URL 提取平台 video id（B 站 BV 号 / YouTube 11 位 / 本地文件名）。"""
    if platform == PLATFORM_BILIBILI:
        # BV 号
        m = re.search(r"(BV[a-zA-Z0-9]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
        # av 号
        m = re.search(r"av(\d+)", url, re.IGNORECASE)
        if m:
            return f"av{m.group(1)}"
    elif platform == PLATFORM_YOUTUBE:
        # youtu.be/<id>
        m = re.search(r"youtu\.be/([\w-]+)", url)
        if m:
            return m.group(1)
        # youtube.com/watch?v=<id>
        m = re.search(r"[?&]v=([\w-]+)", url)
        if m:
            return m.group(1)
        # youtube.com/shorts/<id>
        m = re.search(r"shorts/([\w-]+)", url)
        if m:
            return m.group(1)
    elif platform == PLATFORM_LOCAL:
        return Path(url).stem
    return ""


# --------------------------------------------------------------------------- #
# YtDlpVersionValidator
# --------------------------------------------------------------------------- #


class YtDlpVersionValidator:
    """yt-dlp 版本校验器（启动期一次性）。

    设计要点：
    - 版本格式 YYYY.MM.DD（yt-dlp 官方约定）
    - 字符串字典序比较即可（设计上"字符串比大小 = 日期比大小"在 YYYY.MM.DD 上成立）
    - 失败统一抛 DownloadError(E_DL_002_VERSION_TOO_OLD / E_DL_003_NETWORK)
    """

    def __init__(
        self,
        min_version: str = YTDLP_MIN_VERSION,
    ) -> None:
        self.min_version = min_version

    def check(self) -> str:
        """执行 yt-dlp --version 并返回版本字符串。

        Returns:
            实际版本字符串（如 "2024.12.13"）

        Raises:
            DownloadError(E_DL_003_NETWORK): yt-dlp 未安装或子进程失败
        """
        try:
            import yt_dlp  # noqa: F401  - 用于触发 ImportError
        except ImportError as e:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="yt-dlp 未安装",
                cause=str(e),
                suggestion="pip install yt-dlp 或 pip install -e .[video]",
            )
            raise DownloadError(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                "yt-dlp 未安装；请 pip install -e .[video]",
            ) from e

        try:
            version = getattr(__import__("yt_dlp", fromlist=["version"]), "version", None)
            version_str: str | None = None
            if version is not None:
                # yt_dlp.version.__version__ 是标准属性
                version_str = str(getattr(version, "__version__", None) or "")
            if not version_str:
                # 退化：尝试 yt_dlp.version.version_tuple
                try:
                    from yt_dlp import version as ytv

                    version_str = ".".join(str(p) for p in ytv.version_tuple[:3])
                except Exception:
                    version_str = None
            if not version_str:
                raise DownloadError(
                    E_DL_003_NETWORK,
                    "无法解析 yt-dlp 版本号",
                )
            return version_str
        except DownloadError:
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="yt-dlp 版本检测失败",
                cause=str(e),
                suggestion="重装 yt-dlp：pip install -U yt-dlp",
            )
            raise DownloadError(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                f"yt-dlp 版本检测失败: {e}",
            ) from e

    def compare(self, actual: str) -> bool:
        """字符串字典序比较（YYYY.MM.DD 格式）。"""
        if not actual:
            return False
        a_parts = actual.split(".")[:3]
        b_parts = self.min_version.split(".")[:3]
        # 填充为 3 段整数比较，避免 "2023.7.6" vs "2023.07.06" 字典序异常
        try:
            a_nums = [int(p) for p in a_parts]
            b_nums = [int(p) for p in b_parts]
        except (ValueError, TypeError):
            # 非数字格式时退回字符串字典序
            return actual >= self.min_version
        return a_nums >= b_nums

    def validate(self) -> bool:
        """check + compare 一体化。"""
        actual = self.check()
        if not self.compare(actual):
            register_error(
                ErrorCode.E_DL_002_VERSION_TOO_OLD.value,
                scene=f"yt-dlp 版本过旧（{actual} < {self.min_version}）",
                cause=f"实际版本 {actual} 低于最低要求 {self.min_version}",
                suggestion="pip install -U yt-dlp",
            )
            raise DownloadError(
                E_DL_002_VERSION_TOO_OLD,
                f"yt-dlp 版本 {actual} 过旧；需 >= {self.min_version}",
            )
        logger.info("yt-dlp 版本校验通过: %s", actual)
        return True


# --------------------------------------------------------------------------- #
# CookieInjector
# --------------------------------------------------------------------------- #


class CookieInjector:
    """Cookie 文件安全注入。

    安全约束：
    - Cookie 文件权限必须 0o600（Unix）；Windows 上 stat.S_IWGRP/S_IWOTH 不为 1
    - 文件不存在 → 抛 DownloadError
    - 不打印 Cookie 内容到日志（即使 sha256 也不暴露）
    """

    def __init__(
        self,
        cookie_path: str | Path,
        required_perms: int = COOKIE_REQUIRED_PERMS,
    ) -> None:
        self.cookie_path = Path(cookie_path)
        self.required_perms = required_perms

    def validate_perms(self) -> bool:
        """校验 Cookie 文件权限。"""
        if not self.cookie_path.exists():
            register_error(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                scene="Cookie 文件不存在",
                cause=f"路径 {self.cookie_path} 不存在",
                suggestion="配置正确的 cookie_path 或不传",
                context={"cookie_path": str(self.cookie_path)},
            )
            raise DownloadError(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                f"Cookie 文件不存在: {self.cookie_path}",
            )

        # Windows 上跳过 0o600 硬检查（stat.S_IMODE 仅低 9 位有效，但 Windows
        # ACL 与 Unix 文件权限模型不同）。Windows 上若文件存在即视为通过。
        if os.name == "nt":
            return True

        mode = stat.S_IMODE(self.cookie_path.stat().st_mode)
        if mode != self.required_perms:
            register_error(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                scene="Cookie 文件权限不安全",
                cause=f"当前 {oct(mode)}，要求 {oct(self.required_perms)}",
                suggestion=f"chmod {oct(self.required_perms)[2:]} {self.cookie_path}",
                context={"cookie_path": str(self.cookie_path), "mode": oct(mode)},
            )
            raise DownloadError(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                f"Cookie 文件权限 {oct(mode)} 不安全；需 {oct(self.required_perms)}",
            )
        return True

    def inject_args(self, args: list[str]) -> list[str]:
        """将 --cookies PATH 注入到 yt-dlp 参数。"""
        self.validate_perms()
        return [*args, "--cookies", str(self.cookie_path)]


# --------------------------------------------------------------------------- #
# YouTubeDownloader
# --------------------------------------------------------------------------- #


class YouTubeDownloader:
    """YouTube 平台下载（依赖 yt-dlp Python API）。

    移植自 BiliNote YoutubeDownloader，但做了以下简化：
    - 去掉代理配置（BiliNote 的 ProxyConfigManager 是它们自己的产品策略）
    - 去掉字幕 fetcher（BiliNote 走 InnerTube 直拉；V1.1 我们只做下载）
    - 去掉 save_cover_to_static（V1.1 track-core 不做截图，不做静态服务）
    - 增加 M-010 错误码登记
    """

    def __init__(
        self,
        cookie_path: str | None = None,
        output_dir: str | Path | None = None,
        timeout_sec: int = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        cache_manager: CacheManager | None = None,
    ) -> None:
        self.cookie_path = cookie_path
        self.output_dir = Path(output_dir) if output_dir else None
        self.timeout_sec = timeout_sec
        self._cache = cache_manager

    def _ensure_output_dir(self, override: str | Path | None = None) -> Path:
        """确定输出目录并创建。"""
        out = Path(override) if override else (self.output_dir or Path("./research-output/video"))
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _build_opts(
        self,
        url: VideoURL,
        output_dir: Path,
        skip_download: bool = False,
    ) -> dict[str, Any]:
        """构造 yt-dlp Python API opts（移植自 BiliNote opts 模式）。"""
        opts: dict[str, Any] = {
            "format": DEFAULT_YT_DLP_FORMAT,
            "outtmpl": str(output_dir / DEFAULT_OUTPUT_TEMPLATE),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if skip_download:
            opts["skip_download"] = True
        if self.cookie_path:
            opts["cookiefile"] = str(self.cookie_path)
        _inject_ffmpeg_location(opts)
        return opts

    async def _extract_info_async(
        self,
        url: str,
        opts: dict[str, Any],
        download: bool = True,
    ) -> dict[str, Any]:
        """异步调用 yt-dlp（在线程池跑同步阻塞）。"""

        def _run() -> dict[str, Any]:
            import yt_dlp

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
                if not isinstance(info, dict):
                    # playlist 等复杂情况取首项
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    else:
                        raise DownloadError(
                            E_DL_004_YT_DLP_FAILED,
                            f"yt-dlp 返回非 dict: {type(info).__name__}",
                        )
                return dict(info)

        return await asyncio.to_thread(_run)

    async def get_metadata(self, url: VideoURL) -> dict[str, Any]:
        """仅获取视频元数据（yt-dlp --dump-json 等价）。"""
        if url.platform != PLATFORM_YOUTUBE:
            raise DownloadError(E_DL_001, f"平台不匹配: {url.platform} != youtube")
        out_dir = self._ensure_output_dir()
        opts = self._build_opts(url, out_dir, skip_download=True)
        try:
            return await self._extract_info_async(url.url, opts, download=False)
        except DownloadError:
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_DL_002_YT_DLP_FAILED.value,
                scene="YouTube 元数据获取失败",
                cause=str(e),
                suggestion="检查 URL 是否可手动打开；或更新 yt-dlp",
                context={"url_sha256": _sha256_of(url.url)},
            )
            raise DownloadError(E_DL_004_YT_DLP_FAILED, f"YouTube 元数据获取失败: {e}") from e

    async def download(self, url: VideoURL) -> DownloadResult:
        """下载 YouTube 视频（异步）。"""
        if url.platform != PLATFORM_YOUTUBE:
            raise DownloadError(E_DL_001, f"平台不匹配: {url.platform} != youtube")

        emit_log("info", "YouTube 下载开始", step="download", url=url.url)

        # M-004 缓存命中短路（NFR4）：NFR4 主要针对转写，但下载本身也可短路
        # 简化：只对转写短路（track-foundation 已有约定）。下载仍走正常路径。

        out_dir = self._ensure_output_dir()
        opts = self._build_opts(url, out_dir)

        try:
            info = await self._extract_info_async(url.url, opts, download=True)
        except DownloadError:
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_DL_002_YT_DLP_FAILED.value,
                scene="YouTube 视频下载失败",
                cause=str(e),
                suggestion="检查网络/URL；或配置代理后重试",
                context={"url_sha256": _sha256_of(url.url)},
            )
            raise DownloadError(E_DL_004_YT_DLP_FAILED, f"YouTube 下载失败: {e}") from e

        result = self._info_to_result(info, out_dir, PLATFORM_YOUTUBE)
        emit_log(
            "info",
            f"YouTube 下载成功: {result.video_id} ({result.size_mb:.1f} MB)",
            step="download",
            url=url.url,
            duration_ms=0,
        )
        return result

    @staticmethod
    def _info_to_result(info: dict[str, Any], out_dir: Path, platform: str) -> DownloadResult:
        """yt-dlp info dict → DownloadResult。"""
        video_id = str(info.get("id") or "")
        ext = str(info.get("ext") or "m4a")
        file_path = str(out_dir / f"{video_id}.{ext}")
        size_bytes = int(info.get("filesize") or info.get("filesize_approx") or 0)
        size_mb = round(size_bytes / 1024 / 1024, 2) if size_bytes > 0 else 0.0
        duration = int(info.get("duration") or 0)
        title = str(info.get("title") or "")
        cover_url = info.get("thumbnail")
        return DownloadResult(
            file_path=file_path,
            size_mb=size_mb,
            duration_sec=duration,
            video_id=video_id,
            etag="",  # YouTube 不返回标准 ETag
            platform=platform,
            title=title,
            cover_url=str(cover_url) if cover_url else None,
            raw_info=info,
        )


# --------------------------------------------------------------------------- #
# BilibiliDownloader
# --------------------------------------------------------------------------- #


class BilibiliDownloader:
    """B 站平台下载。

    移植自 BiliNote BilibiliDownloader：
    - 复用 cookiefile + Referer 头
    - 简化：不做字幕 fetcher（V1.1 不在 track-core 范围）
    - 简化：不做 postprocessor 抽 mp3（直接保留 yt-dlp 原始 m4a）
      抽音轨交给 M-009 ffmpeg_wrapper
    - 简化：不写 Netscape 临时 cookie 文件（BiliNote 兼容 2 套 cookie 配置；
      V1.1 只用 cookiefile 一种）
    - 严格不绕过 403（AR 调研 S-101）
    """

    BILI_REFERER: ClassVar[str] = "https://www.bilibili.com"

    def __init__(
        self,
        cookie_path: str | None = None,
        output_dir: str | Path | None = None,
        timeout_sec: int = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        cookie_injector: CookieInjector | None = None,
    ) -> None:
        self.cookie_path = cookie_path
        self.output_dir = Path(output_dir) if output_dir else None
        self.timeout_sec = timeout_sec
        self.cookie_injector = cookie_injector or (
            CookieInjector(cookie_path) if cookie_path else None
        )

    def _ensure_output_dir(self, override: str | Path | None = None) -> Path:
        out = Path(override) if override else (self.output_dir or Path("./research-output/video"))
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _build_opts(
        self,
        url: VideoURL,
        output_dir: Path,
        skip_download: bool = False,
    ) -> dict[str, Any]:
        """构造 yt-dlp opts（B 站专属：Referer + 可能的 cookiefile）。"""
        opts: dict[str, Any] = {
            "format": DEFAULT_YT_DLP_FORMAT,
            "outtmpl": str(output_dir / DEFAULT_OUTPUT_TEMPLATE),
            "http_headers": {"Referer": self.BILI_REFERER},
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if skip_download:
            opts["skip_download"] = True
        if self.cookie_path:
            opts["cookiefile"] = str(self.cookie_path)
        _inject_ffmpeg_location(opts)
        return opts

    @staticmethod
    def _is_403(output: str) -> bool:
        """解析 yt-dlp stderr 判定 403。"""
        if not output:
            return False
        low = output.lower()
        return "403" in low and ("forbidden" in low or "http error 403" in low)

    async def _extract_info_async(
        self,
        url: str,
        opts: dict[str, Any],
        download: bool = True,
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            import yt_dlp

            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=download)
                except yt_dlp.utils.DownloadError as e:
                    msg = str(e)
                    if BilibiliDownloader._is_403(msg):
                        raise DownloadError(E_DL_BILI_403, f"B 站 403：{msg[:200]}")
                    if "403" in msg or "Forbidden" in msg:
                        raise DownloadError(E_DL_BILI_403, f"B 站 403：{msg[:200]}")
                    raise DownloadError(E_DL_004_YT_DLP_FAILED, f"yt-dlp 失败：{msg[:200]}")
                if not isinstance(info, dict):
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    else:
                        raise DownloadError(
                            E_DL_004_YT_DLP_FAILED,
                            f"yt-dlp 返回非 dict: {type(info).__name__}",
                        )
                return dict(info)

        return await asyncio.to_thread(_run)

    async def get_metadata(self, url: VideoURL) -> dict[str, Any]:
        if url.platform != PLATFORM_BILIBILI:
            raise DownloadError(E_DL_001, f"平台不匹配: {url.platform} != bilibili")
        out_dir = self._ensure_output_dir()
        opts = self._build_opts(url, out_dir, skip_download=True)
        try:
            return await self._extract_info_async(url.url, opts, download=False)
        except DownloadError:
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_DL_002_YT_DLP_FAILED.value,
                scene="B 站元数据获取失败",
                cause=str(e),
                suggestion="检查 URL；B 站可能限流/需 Cookie",
                context={"url_sha256": _sha256_of(url.url)},
            )
            raise DownloadError(E_DL_004_YT_DLP_FAILED, f"B 站元数据获取失败: {e}") from e

    async def download(self, url: VideoURL) -> DownloadResult:
        if url.platform != PLATFORM_BILIBILI:
            raise DownloadError(E_DL_001, f"平台不匹配: {url.platform} != bilibili")
        emit_log("info", "B 站下载开始", step="download", url=url.url)
        out_dir = self._ensure_output_dir()
        opts = self._build_opts(url, out_dir)
        try:
            info = await self._extract_info_async(url.url, opts, download=True)
        except DownloadError as e:
            # 403 严格不重试（AR 调研 S-101）
            if e.code == E_DL_BILI_403:
                register_error(
                    ErrorCode.E_DL_BILI_403.value,
                    scene="B 站 403（需要 Cookie）",
                    cause="未提供有效 Cookie / 视频需要登录",
                    suggestion="配置 B 站 SESSDATA Cookie 后重试",
                    context={"url_sha256": _sha256_of(url.url)},
                )
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_DL_002_YT_DLP_FAILED.value,
                scene="B 站下载失败",
                cause=str(e),
                suggestion="检查网络；B 站可能限流",
                context={"url_sha256": _sha256_of(url.url)},
            )
            raise DownloadError(E_DL_004_YT_DLP_FAILED, f"B 站下载失败: {e}") from e

        result = self._info_to_result(info, out_dir, PLATFORM_BILIBILI)
        emit_log(
            "info",
            f"B 站下载成功: {result.video_id} ({result.size_mb:.1f} MB)",
            step="download",
            url=url.url,
        )
        return result

    @staticmethod
    def _info_to_result(info: dict[str, Any], out_dir: Path, platform: str) -> DownloadResult:
        video_id = str(info.get("id") or "")
        ext = str(info.get("ext") or "m4a")
        file_path = str(out_dir / f"{video_id}.{ext}")
        size_bytes = int(info.get("filesize") or info.get("filesize_approx") or 0)
        size_mb = round(size_bytes / 1024 / 1024, 2) if size_bytes > 0 else 0.0
        duration = int(info.get("duration") or 0)
        title = str(info.get("title") or "")
        cover_url = info.get("thumbnail")
        return DownloadResult(
            file_path=file_path,
            size_mb=size_mb,
            duration_sec=duration,
            video_id=video_id,
            etag="",
            platform=platform,
            title=title,
            cover_url=str(cover_url) if cover_url else None,
            raw_info=info,
        )


# --------------------------------------------------------------------------- #
# LocalFileResolver
# --------------------------------------------------------------------------- #


class LocalFileResolver:
    """本地文件解析（无下载步骤，仅校验 + 探测元信息）。

    移植自 BiliNote LocalDownloader，但简化为只做解析：
    - 不抽 mp3（V1.1 track-core 不在范围；交给 M-009）
    - 不抽封面（不在范围）
    - 不在 /uploads 前缀处理（research-tool 没有 web 上传）
    """

    def __init__(
        self,
        supported_ext: tuple[str, ...] = LOCAL_SUPPORTED_EXTS,
    ) -> None:
        self.supported_ext = supported_ext

    def validate(self, path: str) -> bool:
        """校验文件存在 + 可读 + 格式合法。"""
        p = Path(path)
        if not p.exists():
            register_error(
                ErrorCode.E_DL_LOCAL_001.value,
                scene="本地文件不存在",
                cause=f"路径 {path} 不存在",
                suggestion="检查文件路径",
            )
            raise DownloadError(E_DL_LOCAL_001, f"本地文件不存在: {path}")
        if not p.is_file():
            raise DownloadError(E_DL_LOCAL_001, f"不是文件: {path}")
        if not os.access(p, os.R_OK):
            raise DownloadError(E_DL_LOCAL_001, f"文件不可读: {path}")
        ext = p.suffix.lower()
        if ext not in self.supported_ext:
            register_error(
                ErrorCode.E_DL_LOCAL_002.value,
                scene="本地文件格式不支持",
                cause=f"后缀 {ext} 不在支持列表 {self.supported_ext}",
                suggestion=f"使用以下后缀之一: {', '.join(self.supported_ext)}",
            )
            raise DownloadError(
                E_DL_LOCAL_002,
                f"本地文件格式 {ext} 不支持；需 {self.supported_ext}",
            )
        return True

    def resolve(self, path: str) -> DownloadResult:
        """解析本地文件为 DownloadResult。

        同步函数（无网络 I/O，仅 fs stat + ffprobe 调用 ffmpeg — 若使用 ffprobe
        走 ffmpeg_wrapper.probe_duration，是纯 fs+subprocess）。

        设计上：纯本地解析无远程阻塞 → 同步实现，调用方可选择 asyncio.to_thread
        """
        self.validate(path)
        p = Path(path)
        size_bytes = p.stat().st_size
        size_mb = round(size_bytes / 1024 / 1024, 2)
        video_id = p.stem

        # 探测时长：可选，依赖 ffmpeg；不可用时返回 0
        duration = self._probe_duration(p)
        return DownloadResult(
            file_path=str(p.resolve()),
            size_mb=size_mb,
            duration_sec=duration,
            video_id=video_id,
            etag="",
            platform=PLATFORM_LOCAL,
            title=p.stem,
            cover_url=None,
        )

    @staticmethod
    def _probe_duration(p: Path) -> int:
        """探测视频时长（秒），失败返回 0。"""
        try:
            # 用 ffmpeg_wrapper（track-foundation）的 ffprobe 工具
            from .ffmpeg_wrapper import FFmpegInvoker

            inv = FFmpegInvoker()
            d = inv.probe_duration(p)
            return int(d) if d else 0
        except Exception:
            return 0


# --------------------------------------------------------------------------- #
# VideoDownloader 顶层门面（统一 3 个平台入口）
# --------------------------------------------------------------------------- #


class VideoDownloader:
    """视频下载器门面（统一 youtube / bilibili / local 三平台）。

    用法：
        dl = VideoDownloader(output_dir="./research-output/video")
        task = await dl.download(video_url)  # 自动路由平台
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        cookie_path: str | None = None,
        timeout_sec: int = DEFAULT_DOWNLOAD_TIMEOUT_SEC,
        retry_times: int = 1,
        cache_manager: CacheManager | None = None,
        youtube_dl: YouTubeDownloader | None = None,
        bilibili_dl: BilibiliDownloader | None = None,
        local_resolver: LocalFileResolver | None = None,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else Path("./research-output/video")
        self.retry_times = max(0, retry_times)
        self._cache = cache_manager
        self.youtube = youtube_dl or YouTubeDownloader(
            cookie_path=cookie_path,
            output_dir=self.output_dir,
            timeout_sec=timeout_sec,
            cache_manager=cache_manager,
        )
        self.bilibili = bilibili_dl or BilibiliDownloader(
            cookie_path=cookie_path,
            output_dir=self.output_dir,
            timeout_sec=timeout_sec,
        )
        self.local = local_resolver or LocalFileResolver()

    def _resolve_platform(self, url: str) -> str:
        """根据 URL 自动识别平台。"""
        return detect_platform(url)

    def build_video_url(self, url: str) -> VideoURL:
        """构造 VideoURL（自动识别 platform + video_id）。"""
        platform = self._resolve_platform(url)
        if not platform:
            register_error(
                ErrorCode.E_VID_002_INVALID_URL.value,
                scene="URL 平台无法识别",
                cause=f"URL {url[:80]} 不匹配 youtube/bilibili/local",
                suggestion="使用 YouTube/Bilibili 视频完整 URL 或本地文件绝对路径",
            )
            raise DownloadError(E_DL_001, f"无法识别 URL 平台: {url}")
        video_id = extract_video_id(url, platform) if platform != PLATFORM_LOCAL else Path(url).stem
        return VideoURL(platform=platform, url=url, video_id=video_id or None)

    async def _download_with_retry(self, fn, url: VideoURL) -> DownloadResult:
        """带重试的下载（首次 + retry_times 次）。"""
        attempts = self.retry_times + 1
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                return await fn(url)
            except DownloadError as e:
                last_exc = e
                # 403 严格不重试（AR 调研 S-101）
                if e.code == E_DL_BILI_403:
                    raise
                if i == attempts - 1:
                    raise
                # 短暂等待
                await asyncio.sleep(0.5 * (2**i))
        # unreachable
        raise DownloadError(E_DL_003_NETWORK, str(last_exc) if last_exc else "未知错误")

    async def download(self, url: str | VideoURL) -> DownloadResult:
        """统一下载入口（自动路由 + 重试）。"""
        if isinstance(url, str):
            video_url = self.build_video_url(url)
        else:
            video_url = url
        if video_url.platform == PLATFORM_YOUTUBE:
            return await self._download_with_retry(self.youtube.download, video_url)
        if video_url.platform == PLATFORM_BILIBILI:
            return await self._download_with_retry(self.bilibili.download, video_url)
        if video_url.platform == PLATFORM_LOCAL:
            # 本地是纯同步，用 to_thread 包
            return await asyncio.to_thread(self.local.resolve, video_url.url)
        raise DownloadError(E_DL_001, f"不支持的平台: {video_url.platform}")

    async def download_to_task(self, url: str | VideoURL) -> DownloadTask:
        """下载并返回 Pydantic DownloadTask（DE-005）。"""
        result = await self.download(url)
        return result.to_download_task()


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


def _sha256_of(s: str) -> str:
    """快速 sha256 前缀（不导入 hashlib 模块级以省冷启动）。"""
    import hashlib

    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


async def download(
    url: str,
    *,
    output_dir: str | Path | None = None,
    cookie_path: str | None = None,
    retry_times: int = 1,
    cache_manager: CacheManager | None = None,
) -> DownloadResult:
    """模块级便捷函数：自动识别平台 + 下载。"""
    dl = VideoDownloader(
        output_dir=output_dir,
        cookie_path=cookie_path,
        retry_times=retry_times,
        cache_manager=cache_manager,
    )
    return await dl.download(url)


async def resolve_local(path: str) -> DownloadResult:
    """模块级便捷函数：解析本地文件。"""
    resolver = LocalFileResolver()
    return await asyncio.to_thread(resolver.resolve, path)


def validate_yt_dlp_version() -> bool:
    """模块级便捷函数：yt-dlp 版本校验。"""
    return YtDlpVersionValidator().validate()


def inject_cookie(args: list[str], cookie_path: str) -> list[str]:
    """模块级便捷函数：Cookie 注入参数。"""
    return CookieInjector(cookie_path).inject_args(args)


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #


__all__ = [
    # 常量
    "YTDLP_MIN_VERSION",
    "PLATFORM_YOUTUBE",
    "PLATFORM_BILIBILI",
    "PLATFORM_LOCAL",
    "LOCAL_SUPPORTED_EXTS",
    "COOKIE_REQUIRED_PERMS",
    "DEFAULT_YT_DLP_FORMAT",
    "DEFAULT_OUTPUT_TEMPLATE",
    "DEFAULT_DOWNLOAD_TIMEOUT_SEC",
    # 错误码
    "E_DL_001",
    "E_DL_002_VERSION_TOO_OLD",
    "E_DL_003_NETWORK",
    "E_DL_004_YT_DLP_FAILED",
    "E_DL_BILI_403",
    "E_DL_LOCAL_001",
    "E_DL_LOCAL_002",
    # 数据类
    "DownloadResult",
    # 类
    "YtDlpVersionValidator",
    "CookieInjector",
    "YouTubeDownloader",
    "BilibiliDownloader",
    "LocalFileResolver",
    "VideoDownloader",
    # 工具
    "detect_platform",
    "extract_video_id",
    # 模块级便捷函数
    "download",
    "resolve_local",
    "validate_yt_dlp_version",
    "inject_cookie",
]
