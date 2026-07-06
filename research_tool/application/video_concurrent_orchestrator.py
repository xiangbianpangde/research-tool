"""M-012 并发编排器（V1.1 VideoIngest）。

设计依据：
- [DD-001:M-012 concurrent_orchestrator] 4 类 + 5 函数（IC-003/IC-029/IC-030）
- [DD-001:FS-M-012] 单文件 concurrent_orchestrator.py（已接受 FDR-M-012-001）
- [DD-001:MD-M-012] 资源池模式 + 资源探测降级（默认 3 并发，RAM < 8G 降到 2）
- [DD-001:SR-004] 3 并发上限 + 鲁棒性

职责：
- SemaphorePool: 资源池基础设施（asyncio.Semaphore 包装 + 平衡检查）
- TaskGatherer: URL 列表并发编排（IC-003/IC-029）
- ResourceProber: psutil 资源探测（CPU 核数 / 可用 RAM，优雅降级）
- ConcurrencyResolver: 基于 RAM 自动选并发数（2 或 3）
- gather_tasks: 顶层入口；detect_concurrency: 资源探测入口

设计模式：协调器 + 资源池 + 模板方法；单例懒加载避免 import 副作用。
异步：所有阻塞 I/O 用 asyncio.to_thread 包装（避免阻塞事件循环）。
错误：异常隔离（return_exceptions=True）→ 失败任务以 Exception 形式入结果。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from ..common.logging_config import emit_log, get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# 模块级常量（DD-001 IC-030 / SR-004）
# --------------------------------------------------------------------------- #

DEFAULT_CONCURRENCY: int = 3
MIN_CONCURRENCY: int = 2
MAX_URL_COUNT: int = 10  # IC-001 上限（防止单次提交过载）
MIN_RAM_GB: float = 8.0  # 资源探测降级阈值
ACQUIRE_TIMEOUT_SEC: float = 60.0  # 资源池获取超时（防止任务死等）

# 错误码（与 M-010 体系并行；本模块内部字符串常量）
E_LIM_001: str = "E_LIM_001"  # URL 数量超 10
E_LIM_002: str = "E_LIM_002"  # 资源池获取超时

T = TypeVar("T")
R = TypeVar("R")


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrchestratorResult:
    """单任务执行结果（DE-009 复用）。"""

    task_id: str
    url: str
    status: str  # "success" | "failed"
    output: Any | None = None
    error: str | None = None
    duration_ms: int = 0


# --------------------------------------------------------------------------- #
# SemaphorePool（资源池基础设施）
# --------------------------------------------------------------------------- #


class SemaphorePool:
    """asyncio.Semaphore 包装：acquire/release 严格配对 + 超时控制。

    设计要点：
    - 内部维护 _in_use 计数（int）便于测试断言
    - release 不平衡 → RuntimeError（防止计数器漂移）
    - acquire 超时 → OrchestratorError(E_LIM_002)
    """

    def __init__(self, size: int = DEFAULT_CONCURRENCY) -> None:
        if size < 1:
            raise ValueError(f"semaphore size must be >= 1, got {size}")
        self._size = int(size)
        self._sem = asyncio.Semaphore(self._size)
        self._in_use = 0  # 测试可观察

    @property
    def size(self) -> int:
        return self._size

    @property
    def in_use(self) -> int:
        return self._in_use

    async def acquire(self, timeout_sec: float = ACQUIRE_TIMEOUT_SEC) -> None:
        """获取信号量（带超时）。"""
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout_sec)
        except asyncio.TimeoutError as e:
            from ..domain.errors import VideoIngestError

            raise VideoIngestError(
                E_LIM_002,
                f"资源池获取超时: {timeout_sec}s (size={self._size}, in_use={self._in_use})",
            ) from e
        self._in_use += 1

    def release(self) -> None:
        """释放信号量。"""
        if self._in_use <= 0:
            raise RuntimeError(f"SemaphorePool.release without acquire (in_use={self._in_use})")
        self._sem.release()
        self._in_use -= 1

    def stats(self) -> dict[str, int]:
        """可观察的池状态（用于日志/测试）。"""
        return {"size": self._size, "in_use": self._in_use, "available": self._size - self._in_use}


# --------------------------------------------------------------------------- #
# ConcurrencyResolver（基于 RAM 自动选并发数）
# --------------------------------------------------------------------------- #


class ConcurrencyResolver:
    """依据可用 RAM 选并发数（2 或 3，IC-030）。

    决策表：
    - RAM >= 8GB  → 3（默认）
    - RAM < 8GB   → 2（降级）
    - 探测失败    → 3（保持默认，不抛错）
    """

    def __init__(
        self,
        prober: "ResourceProber | None" = None,
        min_ram_gb: float = MIN_RAM_GB,
        fallback: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._prober = prober or ResourceProber()
        self._min_ram_gb = min_ram_gb
        self._fallback = fallback

    def resolve(self, ram_gb: float | None = None) -> int:
        """返回推荐并发数（2 或 3）。

        Args:
            ram_gb: 显式 RAM（GB）；None=用内部探测
        """
        if ram_gb is None:
            ram_gb = self._prober.detect_ram_gb()
        if ram_gb <= 0:
            # 探测失败或极小 RAM → 保守降级
            emit_log(
                "warning",
                f"资源探测失败（ram={ram_gb}），使用默认并发 {self._fallback}",
                step="orch_concurrency",
            )
            return self._fallback
        if ram_gb < self._min_ram_gb:
            return MIN_CONCURRENCY
        return DEFAULT_CONCURRENCY


# --------------------------------------------------------------------------- #
# ResourceProber（psutil 资源探测，优雅降级）
# --------------------------------------------------------------------------- #


class ResourceProber:
    """资源探测器：CPU 核数 / 可用 RAM（GB）。

    优雅降级：
    - psutil 缺失 / 异常 → 返回保守默认值（不抛错）
    - Windows 上 /proc/meminfo 不可用 → 返回 0.0（让 ConcurrencyResolver 走 fallback）
    """

    def __init__(self) -> None:
        self._psutil_available: bool | None = None

    def is_psutil_available(self) -> bool:
        """psutil 是否可用（延迟探测 + 缓存）。"""
        if self._psutil_available is None:
            try:
                import psutil  # noqa: F401

                self._psutil_available = True
            except ImportError:
                self._psutil_available = False
        return self._psutil_available

    def detect_cpu_count(self) -> int:
        """探测 CPU 核数。失败返回 1。"""
        try:
            import psutil

            return int(psutil.cpu_count(logical=True) or 1)
        except ImportError:
            pass
        # 退化：os.cpu_count()
        try:
            return int(os.cpu_count() or 1)
        except Exception:
            return 1

    def detect_ram_gb(self) -> float:
        """探测可用 RAM（GB）。失败返回 0.0。

        V1.1：使用 psutil.virtual_memory().available
        退化路径（POSIX）：/proc/meminfo 的 MemAvailable 行
        """
        try:
            import psutil

            return float(psutil.virtual_memory().available) / 1024 / 1024 / 1024
        except ImportError:
            pass
        if os.name == "posix":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable"):
                            kb = int(line.split()[1])
                            return kb / 1024 / 1024
            except (OSError, ValueError):
                pass
        return 0.0

    def detect_total_ram_gb(self) -> float:
        """探测总 RAM（GB）。失败返回 0.0。"""
        try:
            import psutil

            return float(psutil.virtual_memory().total) / 1024 / 1024 / 1024
        except ImportError:
            pass
        if os.name == "posix":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            kb = int(line.split()[1])
                            return kb / 1024 / 1024
            except (OSError, ValueError):
                pass
        return 0.0


# --------------------------------------------------------------------------- #
# TaskGatherer（URL 列表并发编排）
# --------------------------------------------------------------------------- #


class TaskGatherer:
    """URL 列表 + task_func → 并发执行 + 异常隔离（IC-003 / IC-029）。"""

    def __init__(
        self,
        pool: SemaphorePool | None = None,
        concurrency_resolver: ConcurrencyResolver | None = None,
    ) -> None:
        self._pool = pool or SemaphorePool(DEFAULT_CONCURRENCY)
        self._resolver = concurrency_resolver or ConcurrencyResolver()

    @property
    def pool(self) -> SemaphorePool:
        return self._pool

    async def gather(
        self,
        urls: list[str],
        task_func: Callable[[str], Awaitable[Any]],
        *,
        task_id_prefix: str = "v",
    ) -> list[OrchestratorResult]:
        """并发执行（异常隔离）。

        Args:
            urls: URL 列表（长度 1-10）
            task_func: 异步任务函数（接受 url，返回 output）
            task_id_prefix: 任务 ID 前缀（默认 "v" → v-0, v-1, ...）

        Returns:
            与 urls 等长的结果列表；失败位置为 status="failed"
        """
        if not urls:
            return []
        if len(urls) > MAX_URL_COUNT:
            from ..domain.errors import VideoIngestError

            raise VideoIngestError(
                E_LIM_001,
                f"URL 数量 {len(urls)} 超过上限 {MAX_URL_COUNT}",
            )

        async def _one(idx: int, url: str) -> OrchestratorResult:
            import time

            task_id = f"{task_id_prefix}-{idx}"
            start = time.monotonic()
            await self._pool.acquire()
            try:
                output = await task_func(url)
                elapsed = int((time.monotonic() - start) * 1000)
                return OrchestratorResult(
                    task_id=task_id,
                    url=url,
                    status="success",
                    output=output,
                    duration_ms=elapsed,
                )
            except Exception as e:  # noqa: BLE001 - 异常隔离，任务异常不中断编排
                elapsed = int((time.monotonic() - start) * 1000)
                err_msg = f"{type(e).__name__}: {e}"
                emit_log(
                    "error",
                    f"任务失败: url={url[:60]} err={err_msg[:100]}",
                    step="orch_gather",
                    task_id=task_id,
                    code=getattr(e, "code", ""),
                )
                return OrchestratorResult(
                    task_id=task_id,
                    url=url,
                    status="failed",
                    error=err_msg,
                    duration_ms=elapsed,
                )
            finally:
                self._pool.release()

        # 并发启动（受 Semaphore 池容量约束）
        tasks = [_one(i, url) for i, url in enumerate(urls)]
        return list(await asyncio.gather(*tasks, return_exceptions=False))


# --------------------------------------------------------------------------- #
# 全局单例（懒加载，psutil 探测不污染 import 副作用）
# --------------------------------------------------------------------------- #

_prober: ResourceProber | None = None
_resolver: ConcurrencyResolver | None = None
_default_pool: SemaphorePool | None = None
_default_gatherer: TaskGatherer | None = None


def _get_prober() -> ResourceProber:
    global _prober
    if _prober is None:
        _prober = ResourceProber()
    return _prober


def _get_resolver() -> ConcurrencyResolver:
    global _resolver
    if _resolver is None:
        _resolver = ConcurrencyResolver(prober=_get_prober())
    return _resolver


def _get_pool(concurrency: int | None = None) -> SemaphorePool:
    global _default_pool
    if concurrency is None:
        concurrency = _get_resolver().resolve()
    if _default_pool is None or _default_pool.size != concurrency:
        _default_pool = SemaphorePool(concurrency)
    return _default_pool


def _get_gatherer(concurrency: int | None = None) -> TaskGatherer:
    global _default_gatherer
    pool = _get_pool(concurrency)
    if _default_gatherer is None or _default_gatherer.pool.size != pool.size:
        _default_gatherer = TaskGatherer(pool=pool)
    return _default_gatherer


def reset_singleton() -> None:
    """测试用：重置全局单例。"""
    global _prober, _resolver, _default_pool, _default_gatherer
    _prober = None
    _resolver = None
    _default_pool = None
    _default_gatherer = None


# --------------------------------------------------------------------------- #
# 顶层入口（IC-003 / IC-029 / IC-030）
# --------------------------------------------------------------------------- #


async def gather_tasks(
    urls: list[str],
    task_func: Callable[[str], Awaitable[Any]],
    *,
    concurrency: int | None = None,
) -> list[OrchestratorResult]:
    """并发调度 URL 列表（IC-003 / IC-029）。

    便捷函数：复用全局单例。tests 想要隔离时可显式 new TaskGatherer。

    Args:
        urls: URL 列表（长度 1-10）
        task_func: 异步任务函数（接受 url 字符串）
        concurrency: 并发上限（None=由 ConcurrencyResolver 探测 RAM 自动选）

    Returns:
        与 urls 等长的结果列表

    Raises:
        VideoIngestError(E_LIM_001): URL 数量 > 10
        VideoIngestError(E_LIM_002): 资源池获取超时
    """
    gatherer = _get_gatherer(concurrency)
    return await gatherer.gather(urls, task_func)


def detect_concurrency() -> int:
    """探测可用 RAM，推荐并发数（2 或 3，IC-030）。"""
    return _get_resolver().resolve()


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #

__all__ = [
    # 常量
    "DEFAULT_CONCURRENCY",
    "MIN_CONCURRENCY",
    "MAX_URL_COUNT",
    "MIN_RAM_GB",
    "ACQUIRE_TIMEOUT_SEC",
    # 错误码
    "E_LIM_001",
    "E_LIM_002",
    # 数据类
    "OrchestratorResult",
    # 类
    "SemaphorePool",
    "TaskGatherer",
    "ResourceProber",
    "ConcurrencyResolver",
    # 顶层入口
    "gather_tasks",
    "detect_concurrency",
    # 测试工具
    "reset_singleton",
]
