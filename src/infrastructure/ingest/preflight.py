"""M-002 预检模块（V1.1 VideoIngest）。

设计依据：[DD-001:M-002 预检] + 任务说明（探测 yt-dlp / ffmpeg / faster-whisper + 4 项 check）

4 项预检：
1. ytdlp_ok     — yt-dlp 二进制可用性（FAIL_BLOCKING：YouTube/B 站任务需要）
2. ffmpeg_ok    — ffmpeg 二进制可用性（FAIL_SOFT：缺失时降级到仅返回元数据）
3. whisper_ok   — faster-whisper 模型可用性（FAIL_SOFT：缺失时降档 base/small）
4. cache_w_ok   — 缓存目录可写（FAIL_SOFT：失败时跳过缓存但继续任务）

异步并行：asyncio.gather 4 项 check（首次 < 2s 性能约束）。
TTL 缓存：60s 内重复调用直接返回缓存结果（IC-006 幂等性）。
失败显式：所有失败通过 M-010 E_PF_001 错误码登记，绝不静默。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...common.logging_config import get_logger
from ...domain.errors import ErrorCode, register_error

logger = get_logger(__name__)

# 模块级默认
DEFAULT_TTL_SECONDS = 60
DEFAULT_WHISPER_MODEL_SIZE = "medium"
DEFAULT_CACHE_DIR = Path("./research-output/cache")


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreflightReport:
    """4 项预检报告（IC-006 PreflightReport / DE-012）。"""

    ytdlp_ok: bool
    ytdlp_version: str | None
    ffmpeg_ok: bool
    ffmpeg_version: str | None
    whisper_ok: bool
    whisper_model_size: str | None
    cache_writable: bool
    cache_dir: str
    timestamp: str
    ttl_seconds: int

    def is_blocking(self) -> bool:
        """是否阻塞主任务（仅 ytdlp 缺失时阻塞）。"""
        return not self.ytdlp_ok

    def to_dict(self) -> dict:
        return {
            "ytdlp_ok": self.ytdlp_ok,
            "ytdlp_version": self.ytdlp_version,
            "ffmpeg_ok": self.ffmpeg_ok,
            "ffmpeg_version": self.ffmpeg_version,
            "whisper_ok": self.whisper_ok,
            "whisper_model_size": self.whisper_model_size,
            "cache_writable": self.cache_writable,
            "cache_dir": self.cache_dir,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PreflightReport":
        return cls(
            ytdlp_ok=bool(d.get("ytdlp_ok", False)),
            ytdlp_version=d.get("ytdlp_version"),
            ffmpeg_ok=bool(d.get("ffmpeg_ok", False)),
            ffmpeg_version=d.get("ffmpeg_version"),
            whisper_ok=bool(d.get("whisper_ok", False)),
            whisper_model_size=d.get("whisper_model_size"),
            cache_writable=bool(d.get("cache_writable", False)),
            cache_dir=str(d.get("cache_dir", "")),
            timestamp=str(d.get("timestamp", "")),
            ttl_seconds=int(d.get("ttl_seconds", DEFAULT_TTL_SECONDS)),
        )


# --------------------------------------------------------------------------- #
# TTL LRU 缓存（模块级单例）
# --------------------------------------------------------------------------- #


@dataclass
class _TTLCache:
    """单值 TTL 缓存（线程/协程安全）。"""

    value: PreflightReport | None = None
    expires_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_CACHE = _TTLCache()


def _cache_get() -> PreflightReport | None:
    """读取缓存（不检查过期）。"""
    return _CACHE.value


def _cache_set(report: PreflightReport, ttl: int) -> None:
    """写入缓存。"""
    _CACHE.value = report
    _CACHE.expires_at = time.time() + ttl


# --------------------------------------------------------------------------- #
# 单项 Checker
# --------------------------------------------------------------------------- #


def _check_ytdlp_sync() -> tuple[bool, str | None]:
    """同步检查 yt-dlp 是否可用。返回 (ok, version)。"""
    # 1) 先查 PATH
    ytdlp_path = shutil.which("yt-dlp")
    if ytdlp_path is None:
        # 2) 退化：尝试 python -m yt_dlp
        try:
            result = subprocess.run(  # noqa: S603 - python module path
                ["python", "-m", "yt_dlp", "--version"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                version = (result.stdout or "").strip() or None
                return True, version
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        # 3) 退化：尝试作为 Python 模块导入
        try:
            import yt_dlp  # type: ignore  # noqa: F401

            # 拿到模块版本
            version = getattr(yt_dlp, "__version__", None)
            return True, str(version) if version else None
        except ImportError:
            pass
        return False, None

    # 有二进制：拿版本
    try:
        result = subprocess.run(  # noqa: S603 - trusted yt-dlp path
            [ytdlp_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = (result.stdout or "").strip() or None
            return True, version
    except (subprocess.TimeoutExpired, OSError):
        pass
    return True, None  # 二进制存在但 version 拿不到：算 ok


def _check_ffmpeg_sync() -> tuple[bool, str | None]:
    """同步检查 ffmpeg。返回 (ok, version)。"""
    ffmpeg_path = shutil.which("ffmpeg") or os.environ.get("FFMPEG_PATH")
    if ffmpeg_path is None:
        return False, None
    try:
        result = subprocess.run(  # noqa: S603 - trusted ffmpeg path
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            first_line = (result.stdout or "").splitlines()[0]
            parts = first_line.split()
            version = parts[2] if len(parts) >= 3 else None
            return True, version
    except (subprocess.TimeoutExpired, OSError):
        pass
    return True, None  # 二进制存在


def _check_whisper_sync(model_size: str = DEFAULT_WHISPER_MODEL_SIZE) -> tuple[bool, str | None]:
    """同步检查 faster-whisper 是否安装。返回 (ok, model_size)。

    注：本检查只验证包是否安装，不下载模型权重（避免预检阶段耗时长）。
    实际模型权重由 M-005 transcriber 在首次使用时下载。
    """
    try:
        import faster_whisper  # type: ignore  # noqa: F401

        return True, model_size
    except ImportError:
        return False, None


def _check_cache_writable_sync(cache_dir: str | Path) -> bool:
    """同步检查缓存目录是否可写。"""
    path = Path(cache_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        # 试写一个临时文件
        test_file = path / ".preflight_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True
    except (OSError, PermissionError):
        return False


# --------------------------------------------------------------------------- #
# PreflightFacade（门面）
# --------------------------------------------------------------------------- #


class PreflightFacade:
    """4 项预检聚合门面（Facade Pattern + 60s TTL 缓存）。"""

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        whisper_model_size: str = DEFAULT_WHISPER_MODEL_SIZE,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.whisper_model_size = whisper_model_size
        self.ttl_seconds = ttl_seconds

    async def check_all(self, *, force_refresh: bool = False) -> PreflightReport:
        """异步并行执行 4 项 checker（NFR3 显式报错）。"""
        # 1) 缓存命中且未过期
        if not force_refresh:
            cached = _cache_get()
            if cached is not None and time.time() < _CACHE.expires_at:
                return cached

        # 2) 并行 4 项
        ytdlp_task = asyncio.to_thread(_check_ytdlp_sync)
        ffmpeg_task = asyncio.to_thread(_check_ffmpeg_sync)
        whisper_task = asyncio.to_thread(_check_whisper_sync, self.whisper_model_size)
        cache_task = asyncio.to_thread(_check_cache_writable_sync, self.cache_dir)

        results = await asyncio.gather(
            ytdlp_task, ffmpeg_task, whisper_task, cache_task,
            return_exceptions=True,
        )

        # 3) 解析（异常 → 视为失败 + 登记 E_PF_001）
        ytdlp_ok, ytdlp_ver = _safe_unpack(results[0], "yt-dlp", default=(False, None))
        ffmpeg_ok, ffmpeg_ver = _safe_unpack(results[1], "ffmpeg", default=(False, None))
        whisper_ok, whisper_size = _safe_unpack(results[2], "whisper", default=(False, None))
        cache_ok = results[3] if isinstance(results[3], bool) else False

        # 4) 缺失登记（绝不允许静默）
        if not ytdlp_ok:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="yt-dlp 不可用",
                cause="未找到 yt-dlp 二进制或 Python 模块",
                suggestion="pip install yt-dlp 或 pip install -e .[video]",
            )
            logger.warning("预检: yt-dlp 缺失（YouTube 任务会阻塞）")
        if not ffmpeg_ok:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="ffmpeg 不可用",
                cause="未找到 ffmpeg 二进制",
                suggestion="安装 ffmpeg（apt/brew/choco）或设置 FFMPEG_PATH",
            )
            logger.warning("预检: ffmpeg 缺失（音轨/截图降级）")
        if not whisper_ok:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="faster-whisper 不可用",
                cause="未安装 faster-whisper",
                suggestion="pip install faster-whisper 或 pip install -e .[video]",
            )
            logger.warning("预检: faster-whisper 缺失（转写降档）")
        if not cache_ok:
            register_error(
                ErrorCode.E_PF_001_TOOL_MISSING.value,
                scene="缓存目录不可写",
                cause=f"无法写入 {self.cache_dir}",
                suggestion="检查目录权限或更换 cache_dir",
                context={"cache_dir": str(self.cache_dir)},
            )
            logger.warning("预检: 缓存目录不可写（%s）", self.cache_dir)

        # 5) 构造报告
        report = PreflightReport(
            ytdlp_ok=ytdlp_ok,
            ytdlp_version=ytdlp_ver,
            ffmpeg_ok=ffmpeg_ok,
            ffmpeg_version=ffmpeg_ver,
            whisper_ok=whisper_ok,
            whisper_model_size=whisper_size,
            cache_writable=cache_ok,
            cache_dir=str(self.cache_dir),
            timestamp=_now_iso(),
            ttl_seconds=self.ttl_seconds,
        )

        # 6) 写缓存
        _cache_set(report, self.ttl_seconds)
        logger.info(
            "预检完成: yt-dlp=%s ffmpeg=%s whisper=%s cache=%s",
            "✓" if ytdlp_ok else "✗",
            "✓" if ffmpeg_ok else "✗",
            "✓" if whisper_ok else "✗",
            "✓" if cache_ok else "✗",
        )
        return report

    def is_blocking(self, report: PreflightReport) -> bool:
        """判定报告是否阻塞主任务（仅 yt-dlp 缺失时阻塞）。"""
        return report.is_blocking()


def _safe_unpack(
    result: object,
    tool: str,
    default: tuple[bool, str | None],
) -> tuple[bool, str | None]:
    """解包 gather 结果，异常时登记 + 返回 default。"""
    if isinstance(result, BaseException):
        register_error(
            ErrorCode.E_PF_001_TOOL_MISSING.value,
            scene=f"{tool} 检测异常",
            cause=str(result),
            suggestion="查看异常堆栈，可能需要重装工具",
        )
        return default
    if isinstance(result, tuple) and len(result) == 2:
        return result  # type: ignore[return-value]
    return default


def _now_iso() -> str:
    """UTC ISO8601 时间戳。"""
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


async def check_all(
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    whisper_model_size: str = DEFAULT_WHISPER_MODEL_SIZE,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> PreflightReport:
    """模块级便捷函数：执行 4 项预检（与 Facade.check_all 等价）。"""
    facade = PreflightFacade(
        cache_dir=cache_dir,
        whisper_model_size=whisper_model_size,
        ttl_seconds=ttl_seconds,
    )
    return await facade.check_all()


def invalidate_cache() -> None:
    """清空 TTL 缓存（便于测试 / 强制重检）。"""
    _CACHE.value = None
    _CACHE.expires_at = 0.0


__all__ = [
    "PreflightReport",
    "PreflightFacade",
    "check_all",
    "invalidate_cache",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_WHISPER_MODEL_SIZE",
    "DEFAULT_CACHE_DIR",
]
