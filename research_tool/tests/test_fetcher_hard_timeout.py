"""P6：Fetcher 外层硬超时 — 卡住的 crawl/http 路径必须在墙钟内失败返回。"""

from __future__ import annotations

import asyncio
import time

import pytest

from research_tool.infrastructure.stages.fetcher import HARD_TIMEOUT_GRACE_SEC, Fetcher, FetchResult


@pytest.fixture(autouse=True)
def _isolate_ssrf_dns(monkeypatch):
    """These tests exercise timeout dispatch, not DNS-based SSRF validation."""
    monkeypatch.setattr(
        "research_tool.infrastructure.stages.fetcher.assert_safe_url",
        lambda _url: None,
    )


@pytest.mark.asyncio
async def test_fetch_hard_timeout_on_hanging_httpx(monkeypatch):
    """monkeypatch 内部 httpx 路径为永挂；外层 wait_for 必须在 hard bound 内返回 ok=False。"""
    # timeout_sec=1 + grace=2 → hard=3s（测试用小 grace，不拖慢 suite）
    f = Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=1,
        hard_timeout_grace_sec=2,
        parse_pdf=False,
    )
    assert f.hard_timeout_sec() == 3.0

    async def hang(_url: str) -> FetchResult:
        await asyncio.sleep(120)
        return FetchResult(_url, "should-not-return")

    monkeypatch.setattr(f, "_fetch_httpx", hang)

    t0 = time.monotonic()
    result = await f.fetch("https://example.com/hang")
    elapsed = time.monotonic() - t0

    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert result.markdown == ""
    # 必须远小于 hang 的 120s；允许调度抖动，上限 8s
    assert elapsed < 8.0, f"elapsed {elapsed:.2f}s suggests hang leaked past wait_for"
    assert elapsed >= 2.0, f"elapsed {elapsed:.2f}s too fast; wait_for may not have run"


@pytest.mark.asyncio
async def test_fetch_hard_timeout_on_hanging_crawl(monkeypatch):
    f = Fetcher(
        prefer_crawl4ai=True,
        timeout_sec=1,
        hard_timeout_grace_sec=1,
        parse_pdf=False,
    )
    # 强制走 crawl 分支（即使环境无 crawl4ai）
    f.use_crawl4ai = True

    async def hang(_url: str) -> FetchResult:
        await asyncio.sleep(60)
        return FetchResult(_url, "never")

    monkeypatch.setattr(f, "_fetch_crawl4ai", hang)

    t0 = time.monotonic()
    result = await f.fetch("https://example.com/crawl-hang")
    elapsed = time.monotonic() - t0

    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert elapsed < 6.0


@pytest.mark.asyncio
async def test_fetch_success_not_killed_by_hard_timeout(monkeypatch):
    """正常完成的抓取不应被 hard timeout 误杀。"""
    f = Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=5,
        hard_timeout_grace_sec=5,
        parse_pdf=False,
    )

    async def ok(url: str) -> FetchResult:
        await asyncio.sleep(0.05)
        return FetchResult(url, "# hello\n\nbody text enough")

    monkeypatch.setattr(f, "_fetch_httpx", ok)
    result = await f.fetch("https://example.com/ok")
    assert result.ok is True
    assert "hello" in result.markdown


def test_hard_timeout_sec_formula():
    f = Fetcher(timeout_sec=30)
    assert f.hard_timeout_sec() == 30 + HARD_TIMEOUT_GRACE_SEC
