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

import ipaddress
import socket
from urllib.parse import urlparse

from ..domain.errors import UrlBlockedError

_ALLOWED_SCHEMES = {"http", "https"}

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
    # NOTE: 198.18.0.0/15 (benchmarking) deliberately NOT blocked — some DNS
    # proxies (fake-IP mode, e.g. Clash/Surge) return 198.18.x.x for ALL
    # hostnames; blocking it makes the tool unusable in those environments.
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


def assert_safe_url(url: str) -> None:
    """Raise ``UrlBlockedError`` if ``url`` targets a blocked destination.

    Literal private IPs are rejected without DNS. Hostnames are resolved via
    ``socket.getaddrinfo``; if ANY resolved address is in a blocked network,
    the URL is rejected. DNS failure (``gaierror``) is treated as unsafe
    (raise, not pass) — resolution failure is not a safe pass.

    Raises:
        UrlBlockedError: if the scheme is not http/https, the host is missing,
            or the host (literal or resolved) is in a blocked network.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UrlBlockedError(f"scheme {parsed.scheme!r} not allowed (only http/https)")
    host = parsed.hostname
    if not host:
        raise UrlBlockedError("URL has no hostname")
    # Literal IP?
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # not a literal IP — resolve as hostname below
    else:
        if _is_blocked_ip(ip):
            raise UrlBlockedError(f"literal IP {host} is in a blocked network")
        return  # literal public IP — safe
    # Resolve hostname; reject if any resolved address is blocked
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlBlockedError(f"DNS resolution failed for {host!r}: {e}") from e
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise UrlBlockedError(f"host {host!r} resolves to blocked address {ip}")


__all__ = ["assert_safe_url"]
