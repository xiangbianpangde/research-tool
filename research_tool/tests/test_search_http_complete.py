"""Offline behavioral coverage for the shared search HTTP client."""

from __future__ import annotations

import json
import asyncio

import httpx
import pytest

from research_tool.infrastructure.search import _http


class _Client:
    """Scripted AsyncClient fake that also proves context cleanup."""

    script: list[httpx.Response | Exception] = []
    init_kwargs: list[dict] = []
    gets: list[tuple[str, dict | None]] = []
    exits = 0

    def __init__(self, **kwargs):
        self.init_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        type(self).exits += 1

    async def get(self, url: str, *, params: dict | None = None):
        self.gets.append((url, params))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset_http_state(monkeypatch):
    _Client.script = []
    _Client.init_kwargs = []
    _Client.gets = []
    _Client.exits = 0
    _http.set_default_proxy(None)
    monkeypatch.setattr(_http.httpx, "AsyncClient", _Client)
    yield
    _http.set_default_proxy(None)


def _response(status: int, *, headers: dict[str, str] | None = None, text: str = ""):
    request = httpx.Request("GET", "https://api.example.test/items")
    return httpx.Response(status, headers=headers, text=text, request=request)


@pytest.mark.asyncio
async def test_get_success_merges_headers_params_and_explicit_proxy():
    _http.set_default_proxy("http://default-proxy.test:8080")
    assert _http.get_default_proxy() == "http://default-proxy.test:8080"
    _Client.script = [_response(200, text="ok")]

    response = await _http._get(
        "https://api.example.test/items",
        params={"q": "paper"},
        headers={"X-Test": "yes"},
        accept="text/plain",
        timeout=3,
        proxy="http://explicit-proxy.test:8080",
    )

    assert response.text == "ok"
    assert _Client.gets == [("https://api.example.test/items", {"q": "paper"})]
    kwargs = _Client.init_kwargs[0]
    assert kwargs["headers"]["Accept"] == "text/plain"
    assert kwargs["headers"]["X-Test"] == "yes"
    assert kwargs["proxy"] == "http://explicit-proxy.test:8080"
    assert kwargs["trust_env"] is True
    assert _Client.exits == 1


@pytest.mark.asyncio
async def test_default_proxy_and_retry_after_are_used(monkeypatch):
    sleeps: list[float] = []

    async def sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", sleep)
    _http.set_default_proxy("http://default-proxy.test:8080")
    _Client.script = [_response(429, headers={"Retry-After": "0.25"}), _response(200)]

    await _http._get("https://api.example.test/items", retries=1)

    assert sleeps == [0.25]
    assert _Client.init_kwargs[0]["proxy"] == "http://default-proxy.test:8080"
    assert _Client.init_kwargs[0]["trust_env"] is True


@pytest.mark.asyncio
async def test_retry_status_uses_backoff_when_retry_after_invalid(monkeypatch):
    sleeps: list[float] = []

    async def sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", sleep)
    _Client.script = [
        _response(503, headers={"Retry-After": "tomorrow"}),
        _response(200),
    ]

    await _http._get("https://api.example.test/items", retries=1, backoff_base=2)

    assert sleeps == [2]
    assert _Client.init_kwargs[0]["proxy"] is None
    assert _Client.init_kwargs[0]["trust_env"] is False


@pytest.mark.asyncio
async def test_transport_error_retries_then_propagates_and_closes(monkeypatch):
    sleeps: list[float] = []

    async def sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", sleep)
    request = httpx.Request("GET", "https://api.example.test/items")
    _Client.script = [httpx.ConnectError("offline", request=request)] * 2

    with pytest.raises(httpx.ConnectError, match="offline"):
        await _http._get("https://api.example.test/items", retries=1, backoff_base=3)

    assert sleeps == [3]
    assert _Client.exits == 1


@pytest.mark.asyncio
async def test_non_retryable_status_fails_immediately(monkeypatch):
    async def forbidden_sleep(_delay: float):
        raise AssertionError("a deterministic 404 must not retry")

    monkeypatch.setattr(_http.asyncio, "sleep", forbidden_sleep)
    _Client.script = [_response(404)]

    with pytest.raises(httpx.HTTPStatusError):
        await _http._get("https://api.example.test/items", retries=4)

    assert len(_Client.gets) == 1
    assert _Client.exits == 1


@pytest.mark.asyncio
async def test_retryable_status_on_last_attempt_is_raised():
    _Client.script = [_response(504)]
    with pytest.raises(httpx.HTTPStatusError):
        await _http._get("https://api.example.test/items", retries=0)


@pytest.mark.asyncio
async def test_negative_retries_are_rejected_before_opening_client():
    with pytest.raises(ValueError, match="retries"):
        await _http._get("https://api.example.test/items", retries=-1)
    assert _Client.init_kwargs == []


def test_describe_handles_empty_and_nonempty_messages():
    assert _http.describe(RuntimeError("broken")) == "RuntimeError: broken"
    assert _http.describe(httpx.ConnectError("")) == "ConnectError"


@pytest.mark.asyncio
async def test_default_proxy_is_isolated_between_async_tasks():
    ready = asyncio.Event()
    observed: dict[str, str | None] = {}

    async def worker(name: str, proxy: str):
        _http.set_default_proxy(proxy)
        if name == "second":
            ready.set()
        await ready.wait()
        await asyncio.sleep(0)
        observed[name] = _http.get_default_proxy()

    await asyncio.gather(
        worker("first", "http://first.test:8080"),
        worker("second", "http://second.test:8080"),
    )

    assert observed == {
        "first": "http://first.test:8080",
        "second": "http://second.test:8080",
    }


@pytest.mark.asyncio
async def test_get_json_and_json_parse_error(monkeypatch):
    calls: list[dict] = []

    async def fake_get(_url: str, **kwargs):
        calls.append(kwargs)
        return _response(200, text='{"ok": true}')

    monkeypatch.setattr(_http, "_get", fake_get)
    assert await _http.get_json("https://api.example.test/json", timeout=1) == {"ok": True}
    assert calls == [{"accept": "application/json", "timeout": 1}]

    async def invalid_get(_url: str, **_kwargs):
        return _response(200, text="not-json")

    monkeypatch.setattr(_http, "_get", invalid_get)
    with pytest.raises(json.JSONDecodeError):
        await _http.get_json("https://api.example.test/bad-json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected_accept"),
    [({}, "application/xml, text/xml, */*"), ({"accept": "text/plain"}, "text/plain")],
)
async def test_get_text_sets_default_without_overwriting_explicit(monkeypatch, kwargs, expected_accept):
    seen: list[dict] = []

    async def fake_get(_url: str, **passed):
        seen.append(passed)
        return _response(200, text="payload")

    monkeypatch.setattr(_http, "_get", fake_get)
    assert await _http.get_text("https://api.example.test/feed", **kwargs) == "payload"
    assert seen[0]["accept"] == expected_accept


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"Retry-After": "1.5"}, 1.5),
        ({"Retry-After": "later"}, None),
        ({"Retry-After": "1e309"}, None),
        ({"Retry-After": "-2"}, 0.0),
        ({"Retry-After": "600"}, 60.0),
    ],
)
def test_retry_after_parsing(headers, expected):
    assert _http._retry_after(_response(429, headers=headers)) == expected
