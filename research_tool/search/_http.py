"""REST 后端共享的异步 HTTP 工具：带 429/503 指数退避的 GET。

新增的 semantic_scholar / wikipedia / github 后端均走免费 REST API，
没有官方 SDK，统一用本模块发请求。对可重试状态码（429/503）做指数退避，
对应升级计划「修复 1：可重试异常退避」。
"""

from __future__ import annotations

import asyncio

import httpx

# 触发重试的状态码：限流 / 服务暂不可用 / 网关超时
_RETRY_STATUS = {429, 502, 503, 504}
_DEFAULT_UA = "Mozilla/5.0 (research-tool; +https://github.com/xiangbianpangde/research-tool)"


async def _get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    accept: str = "application/json",
    timeout: float = 20.0,
    retries: int = 3,
    backoff_base: float = 1.5,
) -> httpx.Response:
    """GET，对 429/5xx 与网络错误做指数退避重试，返回 Response（content 已加载）。

    最终仍失败时抛出最后一次异常，交由调用方（后端的 search）包装为
    SearchError，再由 Collector 收集进 warnings。
    """
    merged_headers = {"User-Agent": _DEFAULT_UA, "Accept": accept}
    if headers:
        merged_headers.update(headers)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=merged_headers
    ) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url, params=params)
                if resp.status_code in _RETRY_STATUS and attempt < retries:
                    # 优先遵循 Retry-After，否则指数退避
                    wait = _retry_after(resp) or backoff_base ** (attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                # 4xx（非 429）是确定性错误，重试无意义
                if status is not None and status not in _RETRY_STATUS:
                    raise
                if attempt < retries:
                    await asyncio.sleep(backoff_base ** (attempt + 1))
                    continue
                raise
    # 理论不可达（循环要么 return 要么 raise）
    assert last_exc is not None
    raise last_exc


def describe(e: Exception) -> str:
    """生成非空的异常描述。网络错误（如 httpx.ConnectError）str() 常为空，
    只给类名才能让 warnings 有意义（修复 1 的可观测性）。"""
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


async def get_json(url: str, **kw) -> dict | list:
    """GET 并解析 JSON（带退避重试）。"""
    resp = await _get(url, accept="application/json", **kw)
    return resp.json()


async def get_text(url: str, **kw) -> str:
    """GET 并返回文本（带退避重试）。用于 RSS/XML 等非 JSON 接口。"""
    kw.setdefault("accept", "application/xml, text/xml, */*")
    resp = await _get(url, **kw)
    return resp.text


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
