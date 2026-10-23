"""Task 2 regression tests for bounded DNS guards and OCR subprocesses.

All tests are offline.  The OCR command is the current Python interpreter used as
an inert child-process fixture; it is not an external OCR runtime.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from research_tool.common import url_guard
from research_tool.common.url_guard import UrlGuardBusyError, UrlGuardTimeoutError
from research_tool.domain.errors import StageError, UrlBlockedError
from research_tool.domain.models import PdfIngestConfig
from research_tool.infrastructure.ingest import ocr as ocr_mod
from research_tool.infrastructure.ingest import pdf as pdf_mod
from research_tool.infrastructure.ingest.ocr import CommandOCREngine
from research_tool.infrastructure.ingest.pdf import PdfIngestor
from research_tool.infrastructure.stages import fetcher as fetcher_mod
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher


def _gai(ip: str) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _sleeping_ocr_command() -> str:
    code = (
        "import os, pathlib, sys, time; "
        "root = pathlib.Path(sys.argv[2]); root.mkdir(parents=True, exist_ok=True); "
        # The parent uses existence as readiness: publish only a complete PID.
        "pending = root / 'pid.pending'; "
        "pending.write_text(str(os.getpid()), encoding='ascii'); "
        "pending.replace(root / 'pid'); "
        "time.sleep(5)"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _read_pid(path: Path) -> int:
    return int(path.read_text(encoding="ascii"))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_if_alive(pid: int | None) -> None:
    if pid is None or not _pid_alive(pid):
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        return
    for _ in range(100):
        if not _pid_alive(pid):
            return
        time.sleep(0.01)


@pytest.mark.asyncio
async def test_async_url_guard_does_not_block_loop_and_has_dns_deadline(monkeypatch):
    """A stuck resolver must not freeze unrelated asyncio tasks or run forever."""
    def slow_public_resolver(*_args, **_kwargs):
        time.sleep(0.15)
        return _gai("93.184.216.34")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", slow_public_resolver)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        deadline = asyncio.get_running_loop().time() + 0.05
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0)

    guard = asyncio.create_task(
        url_guard.assert_safe_url_async("https://resolver.test/", timeout_sec=0.03)
    )
    await ticker()

    assert ticks > 5, "DNS resolution blocked the event loop"
    with pytest.raises(UrlGuardTimeoutError, match="DNS|timeout"):
        await guard


@pytest.mark.asyncio
async def test_async_url_guard_rejects_dns_failure_and_private_addresses(monkeypatch):
    def dns_failure(*_args, **_kwargs):
        raise socket.gaierror("fixture DNS failure")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", dns_failure)
    with pytest.raises(UrlBlockedError, match="DNS resolution failed"):
        await url_guard.assert_safe_url_async("https://missing.test/", timeout_sec=0.1)

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", lambda *_a, **_k: _gai("127.0.0.1"))
    with pytest.raises(UrlBlockedError, match="blocked"):
        await url_guard.assert_safe_url_async("https://private.test/", timeout_sec=0.1)


@pytest.mark.asyncio
async def test_async_url_guard_admission_rejects_when_dns_pool_is_full(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    started_count = 0
    started_lock = threading.Lock()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=url_guard.DNS_MAX_IN_FLIGHT)

    def blocked_resolver(*_args, **_kwargs):
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == url_guard.DNS_MAX_IN_FLIGHT:
                started.set()
        release.wait(timeout=1.0)
        return _gai("93.184.216.34")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", blocked_resolver)
    monkeypatch.setattr(url_guard, "_DNS_EXECUTOR", executor)
    monkeypatch.setattr(
        url_guard,
        "_DNS_ADMISSION",
        threading.BoundedSemaphore(url_guard.DNS_MAX_IN_FLIGHT),
    )
    tasks = [
        asyncio.create_task(
            url_guard.assert_safe_url_async(f"https://resolver-{idx}.test/", timeout_sec=0.5)
        )
        for idx in range(url_guard.DNS_MAX_IN_FLIGHT)
    ]
    assert await asyncio.to_thread(started.wait, 1.0)
    with pytest.raises(UrlGuardBusyError, match="busy|capacity|DNS"):
        await url_guard.assert_safe_url_async("https://overflow.test/", timeout_sec=0.5)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    executor.shutdown(wait=True)
    assert all(result is None for result in results)


@pytest.mark.asyncio
async def test_async_url_guard_timeout_is_not_ssrf_block(monkeypatch):
    def slow_public_resolver(*_args, **_kwargs):
        time.sleep(0.15)
        return _gai("93.184.216.34")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", slow_public_resolver)
    with pytest.raises(UrlGuardTimeoutError, match="timeout"):
        await url_guard.assert_safe_url_async("https://timeout.test/", timeout_sec=0.01)


@pytest.mark.asyncio
async def test_fetch_hard_deadline_includes_async_dns_guard(monkeypatch):
    """Entry guarding must consume the same wall clock as page fetching."""
    def slow_public_resolver(*_args, **_kwargs):
        time.sleep(0.2)
        return _gai("93.184.216.34")

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", slow_public_resolver)
    fetcher = Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=0.03,
        hard_timeout_grace_sec=0,
        parse_pdf=False,
    )

    async def should_not_fetch(_url: str) -> FetchResult:
        return FetchResult(_url, "unexpected content")

    monkeypatch.setattr(fetcher, "_fetch_httpx", should_not_fetch)
    result = await fetcher.fetch("https://slow-resolver.test/")

    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert result.markdown == ""


@pytest.mark.asyncio
async def test_redirect_hook_uses_async_guard(monkeypatch):
    seen: list[str] = []

    async def guard(url: str, *, timeout_sec: float = 5.0) -> None:
        del timeout_sec
        seen.append(url)
        if "127.0.0.1" in url:
            raise UrlBlockedError("redirect resolves to blocked address")

    monkeypatch.setattr(fetcher_mod, "assert_safe_url_async", guard)
    monkeypatch.setattr(
        fetcher_mod,
        "assert_safe_url",
        lambda _url: pytest.fail("async fetch used the synchronous URL guard"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    fetcher = Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=1,
        hard_timeout_grace_sec=0,
        parse_pdf=False,
        _transport=httpx.MockTransport(handler),
    )
    result = await fetcher.fetch("http://93.184.216.34/start")

    assert result.ok is False
    assert "SSRF" in result.error
    assert seen[0] == "http://93.184.216.34/start"
    assert "127.0.0.1" in seen[-1]


def test_ocr_timeout_terminates_and_reaps_child(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    parse_root = tmp_path / "ocr"
    config = PdfIngestConfig(ocr_engine="custom", ocr_cmd=_sleeping_ocr_command())
    engine = CommandOCREngine(config, timeout_sec=0.05)
    pid: int | None = None

    started = time.monotonic()
    try:
        with pytest.raises(StageError, match="超时|timeout"):
            engine.parse(pdf, parse_root)
        pid = _read_pid(parse_root / "pid")
        assert time.monotonic() - started < 1.0
        time.sleep(0.05)
        assert not _pid_alive(pid), "timed-out OCR child was left alive"
    finally:
        _terminate_if_alive(pid)


@pytest.mark.asyncio
class _GuardRaisingClient:
    error: BaseException = UrlGuardTimeoutError("guard timeout")

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url):
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("_fetch_pdf", "https://example.test/paper.pdf"),
        ("_fetch_httpx", "https://example.test/page"),
        ("_fetch_github_readme", "https://github.com/owner/repo"),
    ],
)
async def test_internal_fetch_guard_errors_are_not_swallowed(monkeypatch, method, url):
    monkeypatch.setattr(fetcher_mod.httpx, "AsyncClient", _GuardRaisingClient)
    fetcher = Fetcher(prefer_crawl4ai=False, parse_pdf=True)
    with pytest.raises(UrlGuardTimeoutError, match="guard timeout"):
        await getattr(fetcher, method)(url)


@pytest.mark.asyncio
async def test_internal_arxiv_and_pdf_save_guard_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(fetcher_mod.httpx, "AsyncClient", _GuardRaisingClient)
    fetcher = Fetcher(prefer_crawl4ai=False, parse_pdf=True)
    with pytest.raises(UrlGuardTimeoutError, match="guard timeout"):
        await fetcher._arxiv_html_fulltext("1234.1234")
    with pytest.raises(UrlGuardTimeoutError, match="guard timeout"):
        await fetcher._save_pdf_bytes("https://example.test/paper.pdf", "https://example.test/source")


@pytest.mark.asyncio
async def test_fetch_maps_internal_guard_error_to_bounded_failure(monkeypatch):
    async def raise_guard(_url: str) -> FetchResult:
        raise UrlGuardBusyError("guard busy")

    fetcher = Fetcher(prefer_crawl4ai=False, parse_pdf=False)
    monkeypatch.setattr(fetcher, "_fetch_dispatch", raise_guard)
    result = await fetcher.fetch("https://93.184.216.34/page")
    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert "SSRF" not in result.error
    assert result.markdown == ""


def test_sync_ocr_cleanup_has_bounded_final_communicate(monkeypatch):
    class HangingPopen:
        pid = 12345
        returncode = None

        def __init__(self):
            self.wait_calls = 0
            self.communicate_timeouts: list[float | None] = []

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("ocr", timeout)
            return None

        def communicate(self, timeout=None):
            self.communicate_timeouts.append(timeout)
            if timeout is None:
                raise AssertionError("OCR cleanup used unbounded communicate")
            raise subprocess.TimeoutExpired("ocr", timeout)

    monkeypatch.setattr(ocr_mod.os, "killpg", lambda *_args: None)
    process = HangingPopen()
    with pytest.raises(ocr_mod.OCREngineCleanupTimeoutError, match="cleanup|reap|wait"):
        ocr_mod._kill_and_reap(process, deadline_sec=0.02)
    assert process.communicate_timeouts
    assert all(timeout is not None for timeout in process.communicate_timeouts)


@pytest.mark.asyncio
async def test_fetch_maps_dns_busy_to_bounded_failure_not_ssrf(monkeypatch):
    async def busy_guard(_url: str, *, timeout_sec: float = 5.0) -> None:
        del timeout_sec
        raise UrlGuardBusyError("DNS guard capacity exhausted")

    monkeypatch.setattr(fetcher_mod, "assert_safe_url_async", busy_guard)
    result = await Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=0.1,
        hard_timeout_grace_sec=0,
        parse_pdf=False,
    ).fetch("https://busy.test/")
    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert "SSRF" not in result.error
    assert result.markdown == ""


@pytest.mark.asyncio
async def test_fetch_maps_dns_timeout_to_hard_timeout_not_ssrf(monkeypatch):
    async def timeout_guard(_url: str, *, timeout_sec: float = 5.0) -> None:
        del timeout_sec
        raise UrlGuardTimeoutError("DNS timeout")

    monkeypatch.setattr(fetcher_mod, "assert_safe_url_async", timeout_guard)
    result = await Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=0.1,
        hard_timeout_grace_sec=0,
        parse_pdf=False,
    ).fetch("https://timeout.test/")
    assert result.ok is False
    assert "hard timeout" in result.error.lower()
    assert "SSRF" not in result.error
    assert result.markdown == ""


@pytest.mark.asyncio
async def test_pdf_ingest_respects_legacy_mineru_patch(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    calls: list[tuple[Path, str, Path]] = []

    def patched(pdf_path: Path, mineru: str, parse_root: Path, **_kwargs) -> str:
        calls.append((pdf_path, mineru, parse_root))
        return "# patched mineru"

    monkeypatch.setattr(pdf_mod, "mineru_to_markdown", patched)
    monkeypatch.setattr(pdf_mod, "find_mineru", lambda _cmd: "/fake/mineru")
    result = await Fetcher(prefer_crawl4ai=False, pdf_dir=tmp_path, mineru_cmd="mineru")._pdf_bytes_to_md(
        "https://example.test/paper.pdf", b"%PDF"
    )
    assert result.ok and result.markdown == "# patched mineru"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_async_cleanup_has_bounded_final_wait(monkeypatch):
    class NeverReapingProcess:
        pid = 12345
        returncode = None

        def terminate(self):
            return None

        def kill(self):
            return None

        async def wait(self):
            await asyncio.sleep(10)

    monkeypatch.setattr(ocr_mod.os, "killpg", lambda *_args: None)
    process = NeverReapingProcess()
    started = time.monotonic()
    with pytest.raises(ocr_mod.OCREngineCleanupTimeoutError, match="cleanup|reap|wait"):
        await ocr_mod._kill_and_reap_async(process, deadline_sec=0.02)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_pdf_ingest_cancellation_terminates_and_reaps_ocr_child(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    work_dir = tmp_path / "work"
    config = PdfIngestConfig(ocr_engine="custom", ocr_cmd=_sleeping_ocr_command())
    task = asyncio.create_task(PdfIngestor(config).run(pdf, work_dir))
    pid: int | None = None

    try:
        pid_path = work_dir / "_ocr" / "pid"
        for _ in range(100):
            if pid_path.exists():
                pid = _read_pid(pid_path)
                break
            await asyncio.sleep(0.01)
        assert pid is not None, "OCR child did not start"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        assert not _pid_alive(pid), "cancelled OCR child was left alive"
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        _terminate_if_alive(pid)
