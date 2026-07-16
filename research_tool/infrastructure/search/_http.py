"""REST 后端共享的异步 HTTP 工具：带 429/503 指数退避的 GET。

新增的 semantic_scholar / wikipedia / github 后端均走免费 REST API，
没有官方 SDK，统一用本模块发请求。对可重试状态码（429/503）做指数退避，
对应升级计划「修复 1：可重试异常退避」。
"""

from __future__ import annotations

import asyncio
import math
from contextvars import ContextVar

import httpx

# 触发重试的状态码：限流 / 服务暂不可用 / 网关超时
_RETRY_STATUS = {429, 502, 503, 504}
_DEFAULT_UA = "Mozilla/5.0 (research-tool; +https://github.com/xiangbianpangde/research-tool)"
_MAX_RETRY_AFTER_SEC = 60.0

# 模块级默认代理：由 Collector 在搜索前按 config.proxy 设置，供所有走 _http 的
# 后端（wikipedia/openalex/crossref/arxiv/s2/pubmed/github/google_news）及 ddgs/tavily
# 共用。国内访问这些被墙站点需设代理；传给 httpx 的 proxy= 非 None 时覆盖 trust_env。
_default_proxy: ContextVar[str | None] = ContextVar("research_tool_search_proxy", default=None)


def set_default_proxy(proxy: str | None) -> None:
    """设置模块级默认代理（Collector.search_queries 调用）。"""
    _default_proxy.set(proxy)


def get_default_proxy() -> str | None:
    """读取模块级默认代理（ddgs / tavily 等非 _http 调用方使用）。"""
    return _default_proxy.get()


async def _get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    accept: str = "application/json",
    timeout: float = 20.0,
    retries: int = 3,
    backoff_base: float = 1.5,
    proxy: str | None = None,
) -> httpx.Response:
    """GET，对 429/5xx 与网络错误做指数退避重试，返回 Response（content 已加载）。

    proxy 未显式传入时回退模块级默认（set_default_proxy 设置），供国内被墙站点
    走代理。最终仍失败时抛出最后一次异常，交由调用方（后端的 search）包装为
    SearchError，再由 Collector 收集进 warnings。
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")

    merged_headers = {"User-Agent": _DEFAULT_UA, "Accept": accept}
    if headers:
        merged_headers.update(headers)

    # 显式 proxy 优先；否则回退模块级默认（Collector 按 config.proxy 设置）
    effective_proxy = proxy if proxy is not None else get_default_proxy()
    # 无显式代理时关闭 trust_env：避免 dotenv 注入的死 HTTPS_PROXY（如 127.0.0.1:10809
    # 未开）让国内可达/海外直连源（DBLP、OpenAlex、GitHub）全部 ConnectError。
    # 需要代理时走 config.proxy → set_default_proxy，不依赖环境变量隐式劫持。
    use_trust_env = bool(effective_proxy)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=merged_headers,
        proxy=effective_proxy,
        trust_env=use_trust_env,
    ) as client:
        attempt = 0
        while True:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code in _RETRY_STATUS and attempt < retries:
                    # 优先遵循 Retry-After，否则指数退避
                    wait = _retry_after(resp) or backoff_base ** (attempt + 1)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                # 4xx（非 429）是确定性错误，重试无意义
                if status is not None and status not in _RETRY_STATUS:
                    raise
                if attempt < retries:
                    await asyncio.sleep(backoff_base ** (attempt + 1))
                    attempt += 1
                    continue
                raise


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
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return min(max(value, 0.0), _MAX_RETRY_AFTER_SEC)
