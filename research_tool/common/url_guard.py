"""SSRF guard: assert a fetch URL targets a public destination.

Blocks internal/private/link-local/cloud-metadata addresses (literal IP or
DNS-resolved). Stdlib only (ipaddress, socket, urllib.parse); imports
domain.errors for UrlBlockedError.

Wired into Fetcher.fetch (entry chokepoint, covers pdf/crawl4ai/httpx) and
httpx request event_hooks (redirect re-validation). Collector._follow_links
also calls assert_safe_url per-href for cleaner skip-logging.

Residual risks (see SSRF round report):
  - DNS rebinding: resolution happens at T0 (guard) and again at T1 (httpx /
    crawl4ai fetch); a pinned-IP fetch would close the gap (out of scope).
  - crawl4ai: a real browser follows its own redirects/JS nav; the entry guard
    validates only the seed URL.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import re
import socket
import threading
from urllib.parse import parse_qsl, urlparse

from ..domain.errors import UrlBlockedError


class UrlGuardTimeoutError(TimeoutError):
    """DNS guard exceeded its own bounded wait; this is not an SSRF verdict."""


class UrlGuardBusyError(RuntimeError):
    """The bounded DNS guard admission capacity is currently exhausted."""

_ALLOWED_SCHEMES = {"http", "https"}
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-signature",
        "awsaccesskeyid",
    }
)
_SENSITIVE_QUERY_KEY_PATTERN = re.compile(
    r"(?:^|[-_.])(api[-_.]?key|auth|credential|password|secret|signature|token)(?:$|[-_.])"
)


def is_sensitive_url_key(key: str) -> bool:
    """Return whether a URL query/fragment key can carry credentials."""
    normalized = key.strip().lower()
    return normalized in _SENSITIVE_QUERY_KEYS or bool(
        _SENSITIVE_QUERY_KEY_PATTERN.search(normalized)
    )

# Comprehensive block list: private, loopback, link-local, cloud-metadata,
# CGNAT, benchmarking, documentation/anycast, multicast, reserved.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # IPv4
    ipaddress.ip_network("0.0.0.0/8"),  # "this network"
    ipaddress.ip_network("10.0.0.0/8"),  # private
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),  # private
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1 (documentation)
    ipaddress.ip_network("192.88.99.0/24"),  # 6to4 relay anycast
    ipaddress.ip_network("192.168.0.0/16"),  # private
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking/fake-IP, not public-routable
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    # IPv6
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("::/8"),  # reserved
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _BLOCKED_NETWORKS)


DNS_MAX_IN_FLIGHT = 4
_DNS_MAX_WORKERS = DNS_MAX_IN_FLIGHT
_DNS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_DNS_MAX_WORKERS,
    thread_name_prefix="research-tool-dns",
)
_DNS_ADMISSION = threading.BoundedSemaphore(DNS_MAX_IN_FLIGHT)
_DNS_DEFAULT_TIMEOUT_SEC = 5.0


def _validate_url_and_get_hostname(url: str) -> str | None:
    """Validate URL syntax/literal IP and return hostname when DNS is needed."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UrlBlockedError(f"scheme {parsed.scheme!r} not allowed (only http/https)")
    if parsed.username is not None or parsed.password is not None:
        raise UrlBlockedError("URL contains embedded credentials")
    query_keys = {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if any(is_sensitive_url_key(key) for key in query_keys):
        raise UrlBlockedError("URL contains credential query parameters")
    fragment_keys = {key for key, _ in parse_qsl(parsed.fragment, keep_blank_values=True)}
    if any(is_sensitive_url_key(key) for key in fragment_keys):
        raise UrlBlockedError("URL contains credential fragment parameters")
    host = parsed.hostname
    if not host:
        raise UrlBlockedError("URL has no hostname")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if _is_blocked_ip(ip):
        raise UrlBlockedError(f"literal IP {host} is in a blocked network")
    return None


def _check_resolved_addresses(host: str, infos: list[tuple]) -> None:
    """Reject a hostname if any DNS answer targets a blocked network."""
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise UrlBlockedError(f"host {host!r} resolves to blocked address {ip}")


def _resolve_and_check(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlBlockedError(f"DNS resolution failed for {host!r}: {e}") from e
    _check_resolved_addresses(host, infos)


def assert_safe_url(url: str) -> None:
    """Raise ``UrlBlockedError`` if ``url`` targets a blocked destination.

    This synchronous boundary is kept for existing synchronous callers. Async
    fetch paths should use :func:`assert_safe_url_async` so DNS does not run on
    the event-loop thread.
    """
    host = _validate_url_and_get_hostname(url)
    if host is not None:
        _resolve_and_check(host)


async def assert_safe_url_async(
    url: str, *, timeout_sec: float = _DNS_DEFAULT_TIMEOUT_SEC
) -> None:
    """Async SSRF guard with bounded DNS waiting.

    DNS is dispatched to a small process-wide pool. Cancelling the await cannot
    cancel an OS resolver already running in that worker; the bounded pool keeps
    active resolver work finite, and failures/timeouts never pass the guard.
    """
    host = _validate_url_and_get_hostname(url)
    if host is None:
        return
    timeout = max(float(timeout_sec), 0.001)
    if not _DNS_ADMISSION.acquire(blocking=False):
        raise UrlGuardBusyError(
            f"DNS guard capacity exhausted ({DNS_MAX_IN_FLIGHT} in-flight); retry later"
        )
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(_DNS_EXECUTOR, _resolve_and_check, host)
    except BaseException:
        _DNS_ADMISSION.release()
        raise
    future.add_done_callback(lambda _future: _DNS_ADMISSION.release())
    try:
        await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise UrlGuardTimeoutError(
            f"DNS resolution timeout for {host!r} after {timeout:g}s"
        ) from e


__all__ = [
    "UrlGuardBusyError",
    "UrlGuardTimeoutError",
    "assert_safe_url",
    "assert_safe_url_async",
    "is_sensitive_url_key",
]
