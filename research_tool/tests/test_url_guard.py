"""SSRF guard tests: assert_safe_url + Fetcher/Collector integration.

Covers the SSRF hardening round: literal private/loopback/link-local/cloud-metadata
IPs, non-http(s) schemes, DNS-resolved private, DNS failure, httpx redirect-to-private,
and Collector._follow_links skipping a private href.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from research_tool.common.url_guard import assert_safe_url
from research_tool.domain.errors import UrlBlockedError
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import Fetcher, FetchResult


# --------------------------------------------------------------------------- #
# Unit: scheme
# --------------------------------------------------------------------------- #


class TestScheme:
    @pytest.mark.parametrize("scheme", ["file", "gopher", "ftp", "data", "javascript"])
    def test_rejects_non_http(self, scheme: str) -> None:
        with pytest.raises(UrlBlockedError, match="scheme"):
            assert_safe_url(f"{scheme}://example.com/")

    def test_allows_http_literal_public(self) -> None:
        assert_safe_url("http://93.184.216.34/")  # no exception

    def test_allows_https_literal_public(self) -> None:
        assert_safe_url("https://93.184.216.34/")


# --------------------------------------------------------------------------- #
# Unit: literal IP
# --------------------------------------------------------------------------- #


class TestLiteralIP:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "172.16.0.1",
            "100.64.0.1",  # CGNAT
            "0.0.0.1",
            "224.0.0.1",  # multicast
            "240.0.0.1",  # reserved
        ],
    )
    def test_rejects_private_ipv4(self, ip: str) -> None:
        with pytest.raises(UrlBlockedError):
            assert_safe_url(f"http://{ip}/")

    @pytest.mark.parametrize("ip", ["::1", "fe80::1", "fc00::1", "ff00::1"])
    def test_rejects_private_ipv6(self, ip: str) -> None:
        with pytest.raises(UrlBlockedError):
            assert_safe_url(f"http://[{ip}]/")

    @pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "1.1.1.1"])
    def test_allows_public_ipv4(self, ip: str) -> None:
        assert_safe_url(f"http://{ip}/")  # no exception


# --------------------------------------------------------------------------- #
# Unit: DNS resolution
# --------------------------------------------------------------------------- #


def _gai(ip: str) -> list:
    """Build a getaddrinfo result for a single IPv4."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class TestDNS:
    def test_rejects_hostname_resolving_to_private(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("127.0.0.1"))
        with pytest.raises(UrlBlockedError, match="resolves to blocked"):
            assert_safe_url("http://example.com/")

    def test_allows_hostname_resolving_to_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("93.184.216.34"))
        assert_safe_url("http://example.com/")  # no exception

    def test_rejects_if_any_resolved_ip_private(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: _gai("8.8.8.8") + _gai("127.0.0.1"),
        )
        with pytest.raises(UrlBlockedError):
            assert_safe_url("http://example.com/")

    def test_dns_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **k: object) -> object:
            raise socket.gaierror("DNS fail")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        with pytest.raises(UrlBlockedError, match="DNS resolution failed"):
            assert_safe_url("http://nonexistent.invalid/")


# --------------------------------------------------------------------------- #
# Integration: Fetcher
# --------------------------------------------------------------------------- #


class TestFetcherGuard:
    @pytest.mark.asyncio
    async def test_fetch_blocks_literal_metadata_ip(self) -> None:
        f = Fetcher(prefer_crawl4ai=False)
        result = await f.fetch("http://169.254.169.254/latest/meta-data/")
        assert result.ok is False
        assert "SSRF" in result.error

    @pytest.mark.asyncio
    async def test_fetch_blocks_redirect_to_private(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

        f = Fetcher(prefer_crawl4ai=False, _transport=httpx.MockTransport(handler))
        # literal public IP seed → entry guard passes; redirect to 127.0.0.1 → event hook blocks
        result = await f.fetch("http://93.184.216.34/start")
        assert result.ok is False
        assert "SSRF" in result.error

    @pytest.mark.asyncio
    async def test_fetch_allows_public(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>ok</body></html>")

        f = Fetcher(prefer_crawl4ai=False, _transport=httpx.MockTransport(handler))
        result = await f.fetch("http://93.184.216.34/page")
        assert result.ok is True
        assert "ok" in result.markdown


# --------------------------------------------------------------------------- #
# Integration: Collector._follow_links
# --------------------------------------------------------------------------- #


class _SpyFetcher:
    """Duck-typed Fetcher stand-in that records every fetch() call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return FetchResult(url, "ok", ok=True)


class TestFollowLinksGuard:
    @pytest.mark.asyncio
    async def test_follow_links_skips_private_href(self) -> None:
        primary_hit = SearchHit(url="http://93.184.216.34/primary", title="t", source_engine="web")
        primary_fr = FetchResult(
            url="http://93.184.216.34/primary",
            markdown="m",
            links=["http://127.0.0.1/secret", "http://93.184.216.34/secondary"],
        )
        fetched = [(primary_hit, primary_fr)]

        spy = _SpyFetcher()
        sem = asyncio.Semaphore(2)
        col = Collector()
        await col._follow_links(fetched, spy, sem, "topic")

        # The private href was NOT fetched (guard skipped it before calling fetch)
        assert "http://127.0.0.1/secret" not in spy.calls
        # The public secondary href WAS fetched
        assert "http://93.184.216.34/secondary" in spy.calls
