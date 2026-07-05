"""M-012 并发编排器单元测试。

覆盖：
- SemaphorePool 资源池（acquire/release 平衡 + 超时）
- TaskGatherer 并发调度（异常隔离 + 长度校验）
- ResourceProber 资源探测（psutil 缺失降级）
- ConcurrencyResolver 决策表（RAM >= 8G → 3；< 8G → 2；探测失败 → 3）
- gather_tasks / detect_concurrency 顶层入口
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from research_tool.application.video_concurrent_orchestrator import (
    ConcurrencyResolver,
    DEFAULT_CONCURRENCY,
    E_LIM_001,
    E_LIM_002,
    MAX_URL_COUNT,
    MIN_CONCURRENCY,
    MIN_RAM_GB,
    OrchestratorResult,
    ResourceProber,
    SemaphorePool,
    TaskGatherer,
    detect_concurrency,
    gather_tasks,
    reset_singleton,
)
from research_tool.domain.errors import VideoIngestError


# --------------------------------------------------------------------------- #
# SemaphorePool
# --------------------------------------------------------------------------- #


class TestSemaphorePool:
    """资源池测试。"""

    @pytest.mark.asyncio
    async def test_acquire_release_basic(self):
        pool = SemaphorePool(size=2)
        assert pool.in_use == 0
        await pool.acquire(timeout_sec=1.0)
        assert pool.in_use == 1
        pool.release()
        assert pool.in_use == 0

    @pytest.mark.asyncio
    async def test_acquire_release_concurrent(self):
        pool = SemaphorePool(size=2)

        async def hold():
            await pool.acquire()
            await asyncio.sleep(0.05)
            pool.release()

        await asyncio.gather(hold(), hold(), hold())
        assert pool.in_use == 0

    def test_release_without_acquire_raises(self):
        pool = SemaphorePool(size=2)
        with pytest.raises(RuntimeError, match="without acquire"):
            pool.release()

    def test_invalid_size(self):
        with pytest.raises(ValueError, match="size must be >= 1"):
            SemaphorePool(size=0)

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        pool = SemaphorePool(size=1)

        # 占用满
        await pool.acquire(timeout_sec=0.5)
        # 第二次获取应超时
        start = time.monotonic()
        with pytest.raises(VideoIngestError) as exc_info:
            await pool.acquire(timeout_sec=0.2)
        elapsed = time.monotonic() - start
        assert exc_info.value.code == E_LIM_002
        assert elapsed < 0.5  # 没等到 0.5s

        pool.release()

    def test_stats(self):
        pool = SemaphorePool(size=3)
        stats = pool.stats()
        assert stats == {"size": 3, "in_use": 0, "available": 3}


# --------------------------------------------------------------------------- #
# TaskGatherer
# --------------------------------------------------------------------------- #


class TestTaskGatherer:
    """并发编排测试。"""

    @pytest.mark.asyncio
    async def test_gather_success(self):
        pool = SemaphorePool(size=3)
        gatherer = TaskGatherer(pool=pool)

        async def fake_task(url: str) -> str:
            await asyncio.sleep(0.01)
            return f"out-{url}"

        urls = ["url1", "url2", "url3"]
        results = await gatherer.gather(urls, fake_task)

        assert len(results) == 3
        assert all(r.status == "success" for r in results)
        assert [r.output for r in results] == ["out-url1", "out-url2", "out-url3"]

    @pytest.mark.asyncio
    async def test_gather_exception_isolated(self):
        pool = SemaphorePool(size=3)
        gatherer = TaskGatherer(pool=pool)

        async def fake_task(url: str) -> str:
            if url == "url2":
                raise ValueError("simulated failure")
            return f"out-{url}"

        urls = ["url1", "url2", "url3"]
        results = await gatherer.gather(urls, fake_task)

        assert len(results) == 3
        assert results[0].status == "success"
        assert results[1].status == "failed"
        assert "simulated failure" in results[1].error
        assert results[2].status == "success"

    @pytest.mark.asyncio
    async def test_gather_too_many_urls_raises(self):
        pool = SemaphorePool(size=3)
        gatherer = TaskGatherer(pool=pool)

        async def fake_task(url: str) -> str:
            return ""

        urls = [f"url{i}" for i in range(MAX_URL_COUNT + 1)]
        with pytest.raises(VideoIngestError) as exc_info:
            await gatherer.gather(urls, fake_task)
        assert exc_info.value.code == E_LIM_001

    @pytest.mark.asyncio
    async def test_gather_empty(self):
        pool = SemaphorePool(size=3)
        gatherer = TaskGatherer(pool=pool)

        async def fake_task(url: str) -> str:
            return ""

        results = await gatherer.gather([], fake_task)
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrency_limit_respected(self):
        """3 并发上限：6 任务应在 2 批次内完成（每批 3）。"""
        pool = SemaphorePool(size=3)
        gatherer = TaskGatherer(pool=pool)
        active = 0
        peak = 0

        async def fake_task(url: str) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return url

        urls = [f"url{i}" for i in range(6)]
        results = await gatherer.gather(urls, fake_task)
        assert len(results) == 6
        assert peak <= 3
        assert peak >= 2  # 至少 2 个并发（容许调度抖动）


# --------------------------------------------------------------------------- #
# ResourceProber
# --------------------------------------------------------------------------- #


class TestResourceProber:
    """资源探测器测试。"""

    def test_is_psutil_available_returns_bool(self):
        prober = ResourceProber()
        result = prober.is_psutil_available()
        assert isinstance(result, bool)

    def test_detect_cpu_count_positive(self):
        prober = ResourceProber()
        n = prober.detect_cpu_count()
        assert isinstance(n, int)
        assert n >= 1

    def test_detect_ram_returns_float(self):
        prober = ResourceProber()
        ram = prober.detect_ram_gb()
        assert isinstance(ram, (int, float))
        assert ram >= 0.0

    def test_psutil_missing_returns_zero_or_positive(self):
        """psutil 缺失时优雅降级（不抛错）。"""
        prober = ResourceProber()
        with patch.dict("sys.modules", {"psutil": None}):
            # 强制 is_psutil_available = False
            prober._psutil_available = False
            ram = prober.detect_ram_gb()
        # 0.0（Windows）or /proc/meminfo 实际值（POSIX）
        assert ram >= 0.0


# --------------------------------------------------------------------------- #
# ConcurrencyResolver
# --------------------------------------------------------------------------- #


class TestConcurrencyResolver:
    """并发决策器测试。"""

    def test_resolve_high_ram(self):
        resolver = ConcurrencyResolver()
        # RAM >= 8GB → 3
        assert resolver.resolve(ram_gb=16.0) == DEFAULT_CONCURRENCY
        assert resolver.resolve(ram_gb=8.0) == DEFAULT_CONCURRENCY
        assert resolver.resolve(ram_gb=MIN_RAM_GB) == DEFAULT_CONCURRENCY

    def test_resolve_low_ram(self):
        resolver = ConcurrencyResolver()
        # RAM < 8GB → 2
        assert resolver.resolve(ram_gb=4.0) == MIN_CONCURRENCY
        assert resolver.resolve(ram_gb=0.5) == MIN_CONCURRENCY

    def test_resolve_probe_failure_returns_fallback(self):
        resolver = ConcurrencyResolver()
        # RAM = 0 → 探测失败 → 走 fallback
        assert resolver.resolve(ram_gb=0.0) == DEFAULT_CONCURRENCY

    def test_resolve_uses_prober_when_ram_none(self):
        """ram_gb=None 时走内部 prober。"""
        prober = ResourceProber()
        resolver = ConcurrencyResolver(prober=prober)
        # 不抛错即可
        n = resolver.resolve()
        assert n in (MIN_CONCURRENCY, DEFAULT_CONCURRENCY)


# --------------------------------------------------------------------------- #
# 顶层入口
# --------------------------------------------------------------------------- #


class TestModuleEntryPoints:
    """gather_tasks / detect_concurrency 单例入口。"""

    def setup_method(self):
        reset_singleton()

    def test_detect_concurrency_returns_valid(self):
        n = detect_concurrency()
        assert n in (MIN_CONCURRENCY, DEFAULT_CONCURRENCY)

    @pytest.mark.asyncio
    async def test_gather_tasks_via_singleton(self):
        async def fake(url: str) -> str:
            return f"done-{url}"

        results = await gather_tasks(["a", "b"], fake)
        assert len(results) == 2
        assert all(r.status == "success" for r in results)
        assert [r.output for r in results] == ["done-a", "done-b"]

    @pytest.mark.asyncio
    async def test_gather_tasks_respects_explicit_concurrency(self):
        async def fake(url: str) -> str:
            return url

        results = await gather_tasks(["a", "b", "c"], fake, concurrency=2)
        assert len(results) == 3


# --------------------------------------------------------------------------- #
# OrchestratorResult 数据类
# --------------------------------------------------------------------------- #


class TestOrchestratorResult:
    """结果数据类测试。"""

    def test_frozen(self):
        r = OrchestratorResult(task_id="v-0", url="u", status="success", output="out")
        with pytest.raises(Exception):  # FrozenInstanceError
            r.status = "failed"  # type: ignore[misc]

    def test_fields(self):
        r = OrchestratorResult(
            task_id="v-1", url="u1", status="failed", error="boom", duration_ms=123,
        )
        assert r.task_id == "v-1"
        assert r.url == "u1"
        assert r.status == "failed"
        assert r.error == "boom"
        assert r.output is None
        assert r.duration_ms == 123
