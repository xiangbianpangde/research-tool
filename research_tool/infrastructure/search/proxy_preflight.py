"""Fast, credential-safe TCP preflight for explicitly configured proxies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import NamedTuple
from urllib.parse import urlsplit


class ProxyPreflightResult(NamedTuple):
    ok: bool
    address: str | None
    message: str


_DEFAULT_PORTS = {"http": 80, "https": 443, "socks5": 1080}


async def preflight_proxy(
    proxy: str | None,
    *,
    timeout_sec: float = 1.0,
    connector: Callable[[str, int], Awaitable[tuple[object, object]]] | None = None,
) -> ProxyPreflightResult:
    """Check that an explicit proxy accepts TCP connections without exposing credentials."""
    if not proxy or not proxy.strip():
        return ProxyPreflightResult(True, None, "未配置显式代理")
    try:
        parsed = urlsplit(proxy.strip())
        host = parsed.hostname
        if parsed.scheme not in _DEFAULT_PORTS or not host:
            raise ValueError("unsupported proxy URL")
        port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    except ValueError:
        return ProxyPreflightResult(
            False,
            None,
            "代理配置无效；支持 http://、https://、socks5://，且必须包含主机",
        )

    display_host = f"[{host}]" if ":" in host else host
    address = f"{parsed.scheme}://{display_host}:{port}"
    writer = None
    try:
        connector = connector or asyncio.open_connection
        _, writer = await asyncio.wait_for(connector(host, port), timeout=timeout_sec)
    except (OSError, asyncio.TimeoutError):
        return ProxyPreflightResult(
            False,
            address,
            f"代理不可达：{address}；请启动代理，或显式设置 "
            "collector.proxy: null / HTTPS_PROXY= 后直连",
        )
    finally:
        if writer is not None and hasattr(writer, "close"):
            try:
                writer.close()
                wait_closed = getattr(writer, "wait_closed", None)
                if wait_closed is not None:
                    await asyncio.wait_for(wait_closed(), timeout=timeout_sec)
            except (OSError, asyncio.TimeoutError):
                pass
    return ProxyPreflightResult(True, address, f"代理可达：{address}")
