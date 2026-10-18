"""Offline deadlines, finite search budgets and durable partial collection."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from research_tool.common.url_guard import UrlGuardTimeoutError
from research_tool.domain.errors import CollectError
from research_tool.domain.models import CollectorConfig, SourceAudit
from research_tool.infrastructure.search import _http
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages import collector as collector_module
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher
from research_tool.infrastructure.experts.registry import ExpertRegistry
from research_tool.infrastructure.search.github_backend import GitHubBackend
from research_tool.infrastructure.search.base import SearchResult

_LONG = "substantial body " * 20


@pytest.fixture(autouse=True)
def _offline_preflight(monkeypatch):
    class Preflight:
        ok = True
        message = "offline fixture"

    async def preflight(_proxy):
        return Preflight()

    monkeypatch.setattr(collector_module, "preflight_proxy", preflight)


def _config(**overrides) -> CollectorConfig:
    values = {
        "search_engines": ["web"],
        "search_cache": False,
        "timeout_sec": 1,
        "max_concurrent_searches": 2,
        "max_results_per_engine": 2,
        "max_total_results": 20,
        "search_relevance_min_overlap": 0,
        "depth": 2,
        "min_doc_chars": 10,
    }
    values.update(overrides)
    return CollectorConfig(**values)


def _hit(name: str, engine: str = "web") -> SearchHit:
    return SearchHit(url=f"https://example.test/{name}", title=name, source_engine=engine)


def _sources(raw_dir):
    return json.loads((raw_dir / "sources.json").read_text(encoding="utf-8"))


def _audits(raw_dir):
    payload = json.loads((raw_dir / "source-audit.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    return {item["engine"]: SourceAudit.model_validate(item) for item in payload["sources"]}


@pytest.mark.asyncio
async def test_never_returning_search_task_is_bounded_and_warned(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Backend:
        async def search(self, query, max_results, language="both", **kwargs):
            if query == "successful":
                return [_hit("successful")]
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(collector_module, "get_backend", lambda *_args: Backend())
    collector = Collector(_config())
    result = await asyncio.wait_for(
        collector.search_queries(["successful", "never"]), timeout=1.5
    )

    assert started.is_set() and cancelled.is_set()
    assert [item.url for item in result.hits] == [_hit("successful").url]
    assert any("截止时间" in warning or "超时" in warning for warning in result.warnings)
    assert result.source_audits == [SourceAudit(engine="web", attempted=2, hits=1, failed=1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("return_hits", [False, True])
async def test_engine_query_page_sort_matrix_has_finite_request_budget(monkeypatch, return_hits):
    calls: list[tuple[str, str, str | None, int]] = []

    class Backend:
        def __init__(self, engine):
            self.engine = engine

        async def search(self, query, max_results, language="both", **kwargs):
            calls.append((self.engine, query, kwargs["sort"], kwargs["offset"]))
            return [_hit(f"{self.engine}-{len(calls)}", self.engine)] if return_hits else []

    monkeypatch.setattr(collector_module, "get_backend", lambda name, _config: Backend(name))
    collector = Collector(
        _config(
            search_engines=["web", "pubmed"],
            deep_search=True,
            deep_pages=5,
            deep_sorts=["relevance", "date", "citations"],
            max_total_results=1,
        )
    )
    result = await collector.search_queries(["one", "two", "three"])

    # B=min(N,max(E*Q,ceil(M/P)+C-1))=min(90,max(6,2))=6.
    # Full retained capacity does not remove required engine/query coverage.
    assert calls == [
        (engine, query, "relevance", 0)
        for query in ("one", "two", "three") for engine in ("web", "pubmed")
    ]
    assert sum(item.attempted for item in result.source_audits) == 6
    assert len(result.hits) == int(return_hits)
    assert any("预算" in warning and "90" in warning for warning in result.warnings)
    assert sum(item.hits for item in result.source_audits) == 6 * int(return_hits)


@pytest.mark.asyncio
async def test_full_result_capacity_does_not_launch_remaining_budget(monkeypatch):
    started: list[str] = []

    class Backend:
        async def search(self, query, max_results, language="both", **kwargs):
            started.append(query)
            return [_hit(f"{query}-{index}") for index in range(max_results)]

    monkeypatch.setattr(collector_module, "get_backend", lambda *_args: Backend())
    collector = Collector(_config(max_total_results=4, deep_search=True, deep_pages=3))
    result = await collector.search_queries(["first", "second", "third", "fourth"])

    # Finite B=max(Q=4, ceil(4/2)+2-1=3)=4; all explicit queries are
    # covered before retained-full stopping is allowed.
    assert started == ["first", "second", "third", "fourth"]
    assert len(result.hits) == 4
    assert result.source_audits == [SourceAudit(engine="web", attempted=4, hits=8, filtered=4)]
    assert any("预算" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_external_search_cancellation_reaps_all_started_siblings(monkeypatch):
    ready = asyncio.Event()
    active: set[str] = set()
    cancelled: set[str] = set()

    class Backend:
        async def search(self, query, max_results, language="both", **kwargs):
            active.add(query)
            if len(active) == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(query)
                cancelled.add(query)

    monkeypatch.setattr(collector_module, "get_backend", lambda *_args: Backend())
    task = asyncio.create_task(Collector(_config()).search_queries(["first", "second"]))
    try:
        await asyncio.wait_for(ready.wait(), timeout=0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not active
        assert cancelled == {"first", "second"}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_index", [0, 1])
async def test_completed_fetch_is_durable_while_another_waits(monkeypatch, tmp_path, completed_index):
    completed = asyncio.Event()
    release = asyncio.Event()
    raw_dir = tmp_path / "raw"
    hits = [_hit("first"), _hit("second")]
    successful_url = hits[completed_index].url

    async def fetch(self, url):
        if url == successful_url:
            completed.set()
            return FetchResult(url, _LONG, ok=True)
        await release.wait()
        return FetchResult(url, _LONG, ok=True)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    task = asyncio.create_task(Collector(_config()).fetch_and_store("topic", hits, raw_dir))
    try:
        await asyncio.wait_for(completed.wait(), timeout=0.25)
        for _ in range(10):
            if list(raw_dir.glob("*.md")):
                break
            await asyncio.sleep(0)
        files = list(raw_dir.glob("*.md"))
        assert len(files) == 1 and not task.done()
        assert files[0].name.startswith(f"{completed_index + 1:02d}-")
        assert _LONG in files[0].read_text(encoding="utf-8")
        assert [item["url"] for item in _sources(raw_dir)] == [successful_url]
        assert _audits(raw_dir)["web"] == SourceAudit(engine="web", retained=1)
        release.set()
        result = await asyncio.wait_for(task, timeout=0.25)
        assert len(result.files) == len(_sources(raw_dir)) == 2
        assert _audits(raw_dir)["web"].retained == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_fetch_cancellation_keeps_completed_output_and_cleans_sibling(monkeypatch, tmp_path):
    completed = asyncio.Event()
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    raw_dir = tmp_path / "raw"

    async def fetch(self, url):
        if url.endswith("first"):
            completed.set()
            return FetchResult(url, _LONG, ok=True)
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    task = asyncio.create_task(
        Collector(_config()).fetch_and_store("topic", [_hit("first"), _hit("second")], raw_dir)
    )
    try:
        await asyncio.wait_for(completed.wait(), timeout=0.25)
        await asyncio.wait_for(waiting.wait(), timeout=0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert len(list(raw_dir.glob("*.md"))) == 1
        assert [item["url"] for item in _sources(raw_dir)] == [_hit("first").url]
        assert _audits(raw_dir)["web"] == SourceAudit(engine="web", retained=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_partial_fetch_failure_warning_matches_persisted_audit(monkeypatch, tmp_path):
    raw_dir = tmp_path / "raw"

    async def fetch(self, url):
        if url.endswith("bad"):
            return FetchResult(url, "", ok=False, error="backend unavailable")
        return FetchResult(url, _LONG, ok=True)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    result = await Collector(_config()).fetch_and_store(
        "topic", [_hit("good"), _hit("bad")], raw_dir
    )

    assert any("backend unavailable" in warning for warning in result.warnings)
    assert _audits(raw_dir)["web"] == SourceAudit(engine="web", retained=1, fetch_failed=1)
    assert result.source_audits == list(_audits(raw_dir).values())
    assert [item["url"] for item in _sources(raw_dir)] == [_hit("good").url]


@pytest.mark.asyncio
async def test_fetch_exception_is_not_returned_as_success_and_cleans_siblings(monkeypatch, tmp_path):
    completed = asyncio.Event()
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    raw_dir = tmp_path / "raw"

    async def fetch(self, url):
        if url.endswith("good"):
            completed.set()
            return FetchResult(url, _LONG, ok=True)
        if url.endswith("crash"):
            await waiting.wait()
            await completed.wait()
            raise RuntimeError("unexpected fetch crash")
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    with pytest.raises(RuntimeError, match="unexpected fetch crash"):
        await asyncio.wait_for(
            Collector(_config(concurrency=3)).fetch_and_store(
                "topic", [_hit("good"), _hit("crash"), _hit("waiting")], raw_dir
            ),
            timeout=0.25,
        )

    assert cancelled.is_set()
    assert [item["url"] for item in _sources(raw_dir)] == [_hit("good").url]
    assert len(list(raw_dir.glob("*.md"))) == 1
    assert _audits(raw_dir)["web"] == SourceAudit(engine="web", retained=1, fetch_failed=1)


@pytest.mark.asyncio
async def test_incremental_file_index_uses_max_prefix_after_partial_run(monkeypatch, tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "01-old.example-a.md").write_text("old", encoding="utf-8")
    (raw_dir / "03-old.example-c.md").write_text("old", encoding="utf-8")

    async def fetch(self, url):
        return FetchResult(url, _LONG, ok=True)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    result = await Collector(_config()).fetch_and_store("topic", [_hit("new")], raw_dir)
    assert result.files[0].name.startswith("04-")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_http_retry_wait_respects_total_deadline_and_preserves_status(monkeypatch, status):
    sleeps: list[float] = []
    closed = asyncio.Event()
    request = httpx.Request("GET", "https://api.example.test/items")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] <= 0.025

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            closed.set()

        async def get(self, url, *, params=None):
            return httpx.Response(status, request=request, headers={"Retry-After": "60"})

    real_sleep = asyncio.sleep

    async def sleep(delay):
        sleeps.append(delay)
        await real_sleep(delay)

    monkeypatch.setattr(_http.httpx, "AsyncClient", Client)
    monkeypatch.setattr(_http.asyncio, "sleep", sleep)
    with pytest.raises(httpx.HTTPStatusError) as error:
        await asyncio.wait_for(
            _http._get(
                str(request.url), retries=5, backoff_base=10,
                deadline=asyncio.get_running_loop().time() + 0.025,
            ),
            timeout=0.25,
        )

    assert error.value.response.status_code == status
    assert sum(sleeps) <= 0.025
    assert len(sleeps) == 1 and closed.is_set()


@pytest.mark.asyncio
async def test_http_explicit_deadline_cannot_extend_inherited_budget(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    closed = asyncio.Event()

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            closed.set()

        async def get(self, url, *, params=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(_http.httpx, "AsyncClient", Client)
    loop = asyncio.get_running_loop()
    token = _http.set_request_deadline(loop.time() + 0.025)
    task = asyncio.create_task(_http._get("https://api.example.test/", deadline=loop.time() + 10))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.25)
        await asyncio.sleep(0.06)
        assert task.done(), "HTTP explicit deadline extended the caller's remaining budget"
        with pytest.raises(asyncio.TimeoutError):
            await task
        assert cancelled.is_set() and closed.is_set()
    finally:
        _http.reset_request_deadline(token)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_review_f1_all_engines_and_explicit_queries_get_first_combo(monkeypatch):
    calls: list[tuple[str, str, str | None, int]] = []
    engines = ["web", "pubmed", "arxiv"]
    queries = ["required-A", "required-B", "topic"]

    class Backend:
        def __init__(self, engine):
            self.engine = engine

        async def search(self, query, limit, language="both", **kwargs):
            calls.append((self.engine, query, kwargs["sort"], kwargs["offset"]))
            return [_hit(f"{self.engine}-{query}-{index}", self.engine) for index in range(limit)]

    monkeypatch.setattr(collector_module, "get_backend", lambda engine, config: Backend(engine))
    collector = Collector(
        _config(
            search_engines=engines,
            extra_queries=queries[:2],
            deep_search=True,
            deep_pages=3,
            deep_sorts=["relevance", "date", "citations"],
            max_total_results=40,
            max_results_per_engine=8,
            max_concurrent_searches=3,
        )
    )
    result = await collector.search_only("topic")

    # The first sweep must cover all E*Q pairs even when retained M is full.
    assert calls == [
        (engine, query, "relevance", 0) for query in queries for engine in engines
    ]
    assert len(result.hits) == 40
    assert {audit.engine: audit.attempted for audit in result.source_audits} == {
        engine: 3 for engine in engines
    }
    assert any("预算" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_review_f2_scoped_github_has_deadline_and_cancellation(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    inherited: list[float | None] = []

    class Registry:
        def match(self, topic, overlap):
            return []

        def scoped_queries_for(self, matched, topic):
            return [("github", "org:required topic")]

    async def search(self, query, limit):
        # Preserve the old two-positional-argument backend test seam.
        inherited.append(_http.get_request_deadline())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(ExpertRegistry, "load", classmethod(lambda cls, path: Registry()))
    monkeypatch.setattr(GitHubBackend, "search", search)
    collector = Collector(_config(search_engines=["github"], experts_file="unused.yaml"))
    task = asyncio.create_task(collector._expert_scoped_github_hits("topic"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.25)
        hits, warnings = await asyncio.wait_for(task, timeout=1.2)
        assert inherited[0] is not None
        assert cancelled.is_set()
        assert hits == []
        assert any("截止时间" in warning or "超时" in warning for warning in warnings)
        assert _http.get_request_deadline() is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_review_f3_secondary_files_cannot_satisfy_missing_official_primary(
    monkeypatch, tmp_path
):
    official_a = "https://official.test/a"
    official_b = "https://official.test/b"
    secondary_urls = ["https://official.test/extra-a", "https://official.test/extra-b"]
    searched = asyncio.Event()

    async def fetch(self, url):
        if url == official_b:
            return FetchResult(url, "", ok=False, error="official b unavailable")
        links = secondary_urls if url == official_a else []
        return FetchResult(url, _LONG, links=links, ok=True)

    async def search(topic):
        searched.set()
        return SearchResult()

    monkeypatch.setattr(
        collector_module, "assert_safe_url_async", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(Fetcher, "fetch", fetch)
    collector = Collector(_config(depth=3, official_urls=[official_a, official_b]))
    monkeypatch.setattr(collector, "search_only", search)

    with pytest.raises(CollectError, match="未全部抓取成功"):
        await collector.run("topic", tmp_path)

    raw_dir = tmp_path / "raw"
    assert not searched.is_set()
    assert {item["url"] for item in _sources(raw_dir)} == {official_a, *secondary_urls}
    assert len(list(raw_dir.glob("*.md"))) == 3
    assert _audits(raw_dir)["official"].fetch_failed == 1
    assert _audits(raw_dir)["official"].retained == 1


@pytest.mark.asyncio
async def test_review_f4_write_error_still_reaps_waiting_sibling_and_preserves_original(
    monkeypatch, tmp_path
):
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    persisted = asyncio.Event()
    raw_dir = tmp_path / "raw"
    worker_tasks: set[asyncio.Task] = set()
    original_error = OSError("primary raw write failed")
    flush_error = OSError("flush raw write also failed")
    write_text = collector_module.write_text

    async def fetch(self, url):
        worker_tasks.add(asyncio.current_task())
        if url.endswith("waiting"):
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        if url.endswith("kept"):
            return FetchResult(url, _LONG, ok=True)
        await waiting.wait()
        await persisted.wait()
        return FetchResult(url, _LONG, ok=True)

    def fail_raw_write(path, text):
        if "/kept -->" in text:
            write_text(path, text)
            persisted.set()
            return
        if "/broken-a -->" in text or "/broken-b -->" in text:
            if not getattr(fail_raw_write, "failed", False):
                fail_raw_write.failed = True
                raise original_error
            raise flush_error
        write_text(path, text)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    monkeypatch.setattr(collector_module, "write_text", fail_raw_write)
    try:
        with pytest.raises(OSError) as raised:
            await Collector(_config(concurrency=4)).fetch_and_store(
                "topic", [_hit(name) for name in ("kept", "broken-a", "broken-b", "waiting")],
                raw_dir,
            )
        assert cancelled.is_set(), "waiting sibling leaked after a flush OSError"
        assert all(task.done() for task in worker_tasks)
        assert raised.value is original_error
        assert [item["url"] for item in _sources(raw_dir)] == [_hit("kept").url]
        assert _audits(raw_dir)["web"].retained == 1
    finally:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_review_f4a_failure_audit_write_does_not_replace_fetch_error(monkeypatch, tmp_path):
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    persisted = asyncio.Event()
    failed_audit_write = asyncio.Event()
    original_error = RuntimeError("original primary fetch failure")
    persistence_error = OSError("failure audit disk write failed")
    worker_tasks: set[asyncio.Task] = set()
    raw_dir = tmp_path / "raw"
    write_json = collector_module.write_json

    async def fetch(self, url):
        worker_tasks.add(asyncio.current_task())
        if url.endswith("kept"):
            return FetchResult(url, _LONG, ok=True)
        if url.endswith("crash"):
            await waiting.wait()
            await persisted.wait()
            raise original_error
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    def fail_failure_audit(path, payload):
        if path.name == "source-audit.json":
            if any(item["fetch_failed"] for item in payload["sources"]):
                failed_audit_write.set()
                raise persistence_error
            write_json(path, payload)
            if any(item["retained"] for item in payload["sources"]):
                persisted.set()
        else:
            write_json(path, payload)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    monkeypatch.setattr(collector_module, "write_json", fail_failure_audit)
    try:
        with pytest.raises(Exception) as raised:
            await asyncio.wait_for(
                Collector(_config(concurrency=3)).fetch_and_store(
                    "topic", [_hit(name) for name in ("kept", "crash", "waiting")], raw_dir
                ),
                timeout=0.5,
            )
        assert failed_audit_write.is_set()
        assert raised.value is original_error
        assert cancelled.is_set()
        assert all(task.done() for task in worker_tasks)
        assert [item["url"] for item in _sources(raw_dir)] == [_hit("kept").url]
        assert _audits(raw_dir)["web"].retained == 1
        assert _LONG in next(raw_dir.glob("*.md")).read_text(encoding="utf-8")
    finally:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_secondary_dns_guard_shares_fetch_concurrency(monkeypatch, tmp_path):
    active = 0
    maximum = 0
    checked = []

    async def guard(url, *, timeout_sec):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0)
            checked.append(url)
        finally:
            active -= 1

    async def fetch(self, url):
        links = [url + "/one", url + "/two"] if url.count("/") == 3 else []
        return FetchResult(url, _LONG, links=links, ok=True)

    monkeypatch.setattr(collector_module, "assert_safe_url_async", guard)
    monkeypatch.setattr(Fetcher, "fetch", fetch)
    result = await Collector(_config(depth=3, concurrency=2)).fetch_and_store(
        "topic", [_hit(name) for name in ("first", "second", "third")], tmp_path / "raw",
    )
    assert len(checked) == 6
    assert maximum <= 2
    assert len(result.files) == 9
    assert active == 0


@pytest.mark.asyncio
async def test_official_url_guard_is_async_and_bounded(monkeypatch, tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_guard(url, *, timeout_sec):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(collector_module, "assert_safe_url_async", hanging_guard)
    collector = Collector(_config(timeout_sec=1, official_urls=["https://official.test/a"]))

    with pytest.raises(CollectError, match="DNS") as raised:
        await asyncio.wait_for(collector.run("topic", tmp_path), timeout=1.5)
    assert isinstance(raised.value.__cause__, UrlGuardTimeoutError)
    assert entered.is_set()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_secondary_url_guard_is_async_and_bounded(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_guard(url, *, timeout_sec):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(collector_module, "assert_safe_url_async", hanging_guard)
    collector = Collector(_config(timeout_sec=1, depth=3))
    primary = _hit("primary")
    fetched = FetchResult(primary.url, _LONG, links=["https://secondary.test/a"], ok=True)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            collector._follow_links([(primary, fetched)], object(), asyncio.Semaphore(1), "topic"),
            timeout=1.2,
        )
    assert entered.is_set()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_review_f4b_secondary_error_reaps_sibling_and_keeps_primary(monkeypatch, tmp_path):
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    original_error = RuntimeError("original secondary fetch failure")
    secondary_tasks: set[asyncio.Task] = set()
    raw_dir = tmp_path / "raw"
    primary = _hit("primary")

    async def fetch(self, url):
        if url == primary.url:
            return FetchResult(
                url, _LONG, links=[_hit("secondary-crash").url, _hit("secondary-waiting").url],
                ok=True,
            )
        secondary_tasks.add(asyncio.current_task())
        if url.endswith("secondary-crash"):
            await waiting.wait()
            raise original_error
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        collector_module, "assert_safe_url_async", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(Fetcher, "fetch", fetch)
    try:
        with pytest.raises(RuntimeError) as raised:
            await asyncio.wait_for(
                Collector(_config(depth=3, concurrency=2)).fetch_and_store(
                    "topic", [primary], raw_dir
                ),
                timeout=0.5,
            )
        assert raised.value is original_error
        assert waiting.is_set()
        assert cancelled.is_set(), "secondary sibling leaked after another secondary failed"
        assert len(secondary_tasks) == 2
        assert all(task.done() for task in secondary_tasks)
        assert [item["url"] for item in _sources(raw_dir)] == [primary.url]
        assert _audits(raw_dir)["web"].retained == 1
        assert _LONG in next(raw_dir.glob("*.md")).read_text(encoding="utf-8")
    finally:
        for task in secondary_tasks:
            task.cancel()
        await asyncio.gather(*secondary_tasks, return_exceptions=True)
