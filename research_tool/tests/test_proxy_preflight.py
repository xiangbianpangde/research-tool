from __future__ import annotations

import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search.proxy_preflight import preflight_proxy
from research_tool.infrastructure.stages import collector as collector_module
from research_tool.infrastructure.stages.collector import Collector


@pytest.mark.asyncio
async def test_empty_proxy_needs_no_connection():
    async def forbidden_connector(_host, _port):
        raise AssertionError("empty proxy must not open a connection")

    result = await preflight_proxy(None, connector=forbidden_connector)

    assert result.ok is True
    assert result.address is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://proxy.example", "http://proxy.example:80"),
        ("https://proxy.example", "https://proxy.example:443"),
        ("socks5://proxy.example", "socks5://proxy.example:1080"),
    ],
)
async def test_proxy_default_ports_are_checked(url, expected):
    calls = []

    async def connector(host, port):
        calls.append((host, port))
        return None, None

    result = await preflight_proxy(url, connector=connector)

    assert result.ok is True
    assert result.address == expected
    assert calls == [("proxy.example", int(expected.rsplit(":", 1)[1]))]


@pytest.mark.asyncio
async def test_unreachable_proxy_fails_without_leaking_credentials():
    async def connector(_host, _port):
        raise OSError("connection refused")

    result = await preflight_proxy(
        "http://alice:secret@127.0.0.1:10809/path?token=hidden",
        connector=connector,
    )

    assert result.ok is False
    assert result.address == "http://127.0.0.1:10809"
    assert "alice" not in result.message
    assert "secret" not in result.message
    assert "hidden" not in result.message
    assert "collector.proxy: null" in result.message


@pytest.mark.asyncio
async def test_proxy_writer_close_failure_does_not_break_success_result():
    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            raise OSError("peer reset during close")

    async def connector(_host, _port):
        return None, Writer()

    result = await preflight_proxy(
        "http://proxy.example:8080",
        connector=connector,
        timeout_sec=0.01,
    )

    assert result.ok is True


@pytest.mark.asyncio
async def test_collector_preflights_explicit_proxy_once(monkeypatch):
    calls = []

    async def fake_preflight(proxy):
        calls.append(proxy)
        return type("Result", (), {"ok": True, "message": "ok"})()

    class Backend:
        async def search(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(collector_module, "preflight_proxy", fake_preflight)
    monkeypatch.setattr(collector_module, "get_backend", lambda *_args: Backend())
    collector = Collector(
        CollectorConfig(
            proxy="http://127.0.0.1:10809",
            search_engines=["web"],
            search_cache=False,
        )
    )

    await collector.search_queries(["first"])
    await collector.search_queries(["second"])

    assert calls == ["http://127.0.0.1:10809"]
