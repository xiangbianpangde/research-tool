"""M-009 ffmpeg 包装（V1.1 VideoIngest）。

职责：抽音轨 + 关键帧截图（不实现完整 BiliNote，仅做最简包装）
- FFmpegInvoker：ffmpeg/ffprobe 子进程调用
- AudioExtractor：抽音轨（mp3/wav）
- KeyframeCapture：I 帧截图
- ffmpeg_path 可通过 config 或环境变量 FFMPEG_PATH 覆盖

失败处理：所有 ffmpeg 失败通过 M-010 E_FM_001 错误码登记，调用方决定降级。
异步：asyncio.to_thread 包同步 subprocess.run，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...common.logging_config import get_logger
from ...domain.errors import ErrorCode, FFmpegError, register_error

logger = get_logger(__name__)

# 模块级默认
DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_AUDIO_FORMAT = "mp3"  # 默认抽 mp3（faster-whisper 推荐）
DEFAULT_KEYFRAME_COUNT = 5
DEFAULT_KEYFRAME_DIRNAME = "keyframes"


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AudioExtractResult:
    """抽音轨结果。"""

    audio_path: Path
    duration_sec: float
    sample_rate: int
    format: str


@dataclass(frozen=True)
class KeyframeResult:
    """单张关键帧截图。"""

    path: Path
    timestamp_sec: float
    width: int
    height: int
    size_bytes: int


# --------------------------------------------------------------------------- #
# FFmpegInvoker
# --------------------------------------------------------------------------- #


class FFmpegInvoker:
    """ffmpeg/ffprobe 子进程调用包装。

    失败统一登记 E_FM_001 并抛出 FFmpegError。
    """

    def __init__(
        self,
        ffmpeg_path: str = DEFAULT_FFMPEG,
        ffprobe_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path or DEFAULT_FFMPEG
        # ffprobe 默认同前缀（ffmpeg -> ffprobe）
        self.ffprobe_path = ffprobe_path or (
            str(Path(self.ffmpeg_path).with_name("ffprobe"))
            if Path(self.ffmpeg_path).parent != Path("")
            else "ffprobe"
        )
        self.timeout_sec = timeout_sec

    def is_available(self) -> bool:
        """ffmpeg 是否就绪。"""
        return shutil.which(self.ffmpeg_path) is not None or Path(self.ffmpeg_path).exists()

    def version(self) -> str | None:
        """获取 ffmpeg 版本（"6.0"），未安装返回 None。"""
        if not self.is_available():
            return None
        try:
            result = subprocess.run(  # noqa: S603 - trusted ffmpeg path
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # 第一行形如 "ffmpeg version 6.0 ..."
                first_line = (result.stdout or "").splitlines()[0]
                parts = first_line.split()
                if len(parts) >= 3:
                    return parts[2]
        except (subprocess.TimeoutExpired, OSError):
            pass
        return None

    def run_sync(
        self,
        args: list[str],
        *,
        timeout_sec: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """同步执行 ffmpeg 命令（内部用，asyncio.to_thread 包装）。"""
        cmd = [self.ffmpeg_path, *args]
        try:
            return subprocess.run(  # noqa: S603 - trusted ffmpeg path
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec or self.timeout_sec,
            )
        except subprocess.TimeoutExpired as e:
            register_error(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                scene="ffmpeg 调用超时",
                cause=f"命令在 {self.timeout_sec}s 内未完成",
                suggestion="调大 timeout_sec，或检查视频文件是否损坏",
                context={"args": args[:5]},
            )
            raise FFmpegError(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                f"ffmpeg 超时（{self.timeout_sec}s）",
            ) from e
        except FileNotFoundError as e:
            register_error(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                scene="ffmpeg 二进制不存在",
                cause=f"未找到可执行文件: {self.ffmpeg_path}",
                suggestion="安装 ffmpeg 或设置 FFMPEG_PATH 环境变量",
            )
            raise FFmpegError(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                f"ffmpeg 不存在: {self.ffmpeg_path}",
            ) from e
        except OSError as e:
            register_error(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                scene="ffmpeg 子进程异常",
                cause=str(e),
                suggestion="检查 ffmpeg 安装完整性",
            )
            raise FFmpegError(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                f"ffmpeg 异常: {e}",
            ) from e

    async def run(
        self,
        args: list[str],
        *,
        timeout_sec: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """异步执行（用 asyncio.to_thread 包装同步 subprocess.run）。"""
        return await asyncio.to_thread(self.run_sync, args, timeout_sec=timeout_sec)

    def probe_duration(self, video_path: str | Path) -> float | None:
        """探测视频时长（秒）。失败返回 None。"""
        try:
            result = subprocess.run(  # noqa: S603 - trusted ffprobe path
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except (subprocess.TimeoutExpired, OSError, ValueError, json.JSONDecodeError):
            return None
        return None


# --------------------------------------------------------------------------- #
# AudioExtractor
# --------------------------------------------------------------------------- #


class AudioExtractor:
    """抽音轨（mp3/wav）—— 给 faster-whisper 用。"""

    def __init__(self, invoker: FFmpegInvoker | None = None) -> None:
        self.invoker = invoker or FFmpegInvoker()

    async def extract(
        self,
        video_path: str | Path,
        output_path: str | Path,
        *,
        format: str = DEFAULT_AUDIO_FORMAT,
        sample_rate: int = 16000,
    ) -> AudioExtractResult:
        """异步抽音轨。

        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径
            format: 输出格式（"mp3"/"wav"）
            sample_rate: 采样率（Whisper 推荐 16000）

        Returns:
            AudioExtractResult 含实际时长/采样率/路径

        Raises:
            FFmpegError: ffmpeg 调用失败（E_FM_001）
        """
        video_path = str(video_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 强制覆盖输出（-y），单音轨（-vn），指定采样率与编码
        args = [
            "-y",
            "-i",
            video_path,
            "-vn",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",  # 单声道
            "-c:a",
            "libmp3lame" if format == "mp3" else "pcm_s16le",
            str(output_path),
        ]
        result = await self.invoker.run(args)
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            register_error(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                scene="抽音轨失败",
                cause=f"ffmpeg exit={result.returncode}, stderr={stderr_tail}",
                suggestion="检查视频文件是否有音轨；尝试 format='wav'",
                context={"video": video_path, "output": str(output_path)},
            )
            raise FFmpegError(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                f"抽音轨失败: exit={result.returncode}",
            )

        duration = self.invoker.probe_duration(output_path) or 0.0
        size_bytes = output_path.stat().st_size if output_path.exists() else 0
        logger.info(
            "抽音轨成功: %s (%.1fs, %d bytes)",
            output_path.name,
            duration,
            size_bytes,
        )
        return AudioExtractResult(
            audio_path=output_path,
            duration_sec=duration,
            sample_rate=sample_rate,
            format=format,
        )


# --------------------------------------------------------------------------- #
# KeyframeCapture
# --------------------------------------------------------------------------- #


class KeyframeCapture:
    r"""关键帧（I 帧）截图。

    用 ffmpeg `-vf select=eq(pict_type\,I)` 选择 I 帧，等距分布。
    默认 5 张，可调 1-10。
    """

    def __init__(self, invoker: FFmpegInvoker | None = None) -> None:
        self.invoker = invoker or FFmpegInvoker()

    async def capture(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        *,
        count: int = DEFAULT_KEYFRAME_COUNT,
        video_id: str | None = None,
    ) -> list[KeyframeResult]:
        """异步抽取关键帧。

        Args:
            video_path: 视频文件路径
            output_dir: 输出目录
            count: 期望帧数（1-10）
            video_id: 视频 ID（用于文件命名；None=用 video_path 的 stem）

        Returns:
            KeyframeResult 列表；失败时返回空列表

        Raises:
            FFmpegError: ffmpeg 调用失败（E_FM_001）
        """
        count = max(1, min(count, 10))
        video_path = str(video_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        vid = video_id or Path(video_path).stem

        # 先探测时长，计算每帧间隔
        duration = self.invoker.probe_duration(video_path) or 0.0

        # 用 fps filter 抽帧：每 (duration/count) 秒抽 1 帧
        if duration > 0 and count > 0:
            interval = max(duration / count, 0.5)  # 最少 0.5s 间隔
        else:
            interval = 10.0  # fallback

        # 输出模板：output_dir/vid_NNN.jpg
        pattern = str(output_dir / f"{vid}_%03d.jpg")

        # ffmpeg 命令：抽帧 + 缩放（最长边 1280）+ 质量 85
        # 注意：filter graph 用 ',' 分隔多个 filter（不转义）；scale 内 'min(1280,iw)'
        # 的参数逗号必须转义为 '\,'，否则会被当作 filter 分隔符吃掉。
        vf = f"fps=1/{interval:g},scale='min(1280\\,iw)':-2"
        args = [
            "-y",
            "-i",
            video_path,
            "-vf",
            vf,
            "-q:v",
            "2",  # JPEG 质量（2=高质量）
            "-frames:v",
            str(count),
            pattern,
        ]
        result = await self.invoker.run(args, timeout_sec=max(60, count * 10))
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-300:]
            register_error(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                scene="关键帧截图失败",
                cause=f"ffmpeg exit={result.returncode}, stderr={stderr_tail}",
                suggestion="检查视频文件是否可读；减小 count 重试",
                context={"video": video_path, "count": count},
            )
            raise FFmpegError(
                ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
                f"截图失败: exit={result.returncode}",
            )

        # 收集生成的帧
        frames: list[KeyframeResult] = []
        for idx in range(1, count + 1):
            path = output_dir / f"{vid}_{idx:03d}.jpg"
            if not path.exists():
                continue
            size = path.stat().st_size
            # 估算时间戳
            ts = (idx - 1) * interval if duration > 0 else 0.0
            frames.append(
                KeyframeResult(
                    path=path,
                    timestamp_sec=ts,
                    width=0,  # 不读图，省 IO
                    height=0,
                    size_bytes=size,
                )
            )
        logger.info(
            "关键帧截图成功: %d/%d 帧 (%s)",
            len(frames),
            count,
            output_dir.name,
        )
        return frames


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


def get_ffmpeg_invoker(ffmpeg_path: str | None = None) -> FFmpegInvoker:
    """创建 FFmpegInvoker（便捷入口）。"""
    import os

    return FFmpegInvoker(ffmpeg_path=ffmpeg_path or os.environ.get("FFMPEG_PATH", DEFAULT_FFMPEG))


async def extract_audio(
    video_path: str | Path,
    output_path: str | Path,
    *,
    ffmpeg_path: str | None = None,
    format: str = DEFAULT_AUDIO_FORMAT,
    sample_rate: int = 16000,
) -> AudioExtractResult:
    """模块级便捷函数：抽音轨。"""
    extractor = AudioExtractor(get_ffmpeg_invoker(ffmpeg_path))
    return await extractor.extract(video_path, output_path, format=format, sample_rate=sample_rate)


async def capture_keyframes(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    ffmpeg_path: str | None = None,
    count: int = DEFAULT_KEYFRAME_COUNT,
    video_id: str | None = None,
) -> list[KeyframeResult]:
    """模块级便捷函数：抽关键帧。"""
    capturer = KeyframeCapture(get_ffmpeg_invoker(ffmpeg_path))
    return await capturer.capture(video_path, output_dir, count=count, video_id=video_id)


__all__ = [
    "FFmpegInvoker",
    "AudioExtractor",
    "AudioExtractResult",
    "KeyframeCapture",
    "KeyframeResult",
    "get_ffmpeg_invoker",
    "extract_audio",
    "capture_keyframes",
    "DEFAULT_KEYFRAME_COUNT",
    "DEFAULT_AUDIO_FORMAT",
    "DEFAULT_KEYFRAME_DIRNAME",
]
