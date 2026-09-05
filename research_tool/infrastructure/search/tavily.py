"""Tavily 搜索后端（需 API Key，稳定高质量）。

依据决策：搜索后端含 Tavily。Key 来自 CollectorConfig.tavily_api_key
（可由 ${TAVILY_API_KEY} 注入）。

支持多 key 轮询：api_key 可以是逗号/分号分隔的多个 key（或通过环境变量
TAVILY_KEYS 提供额外 key）。当某个 key 触发 Tavily 配额限制
（UsageLimitExceededError，HTTP 429/432/433）时，自动轮换到下一个 key
重试；所有 key 均耗尽才抛 SearchError。单 key 时行为与之前完全一致。
"""

from __future__ import annotations

import asyncio
import os

from ...domain.errors import SearchError
from ._http import get_default_proxy
from .base import SearchBackend, SearchHit


def _split_keys(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.replace(";", ",").split(",") if k.strip()]


class TavilyBackend(SearchBackend):
    name = "tavily"

    def __init__(self, api_key: str | None) -> None:
        pool = _split_keys(api_key)
        for extra in _split_keys(os.environ.get("TAVILY_KEYS")):
            if extra not in pool:
                pool.append(extra)
        if not pool:
            raise SearchError(
                "Tavily 后端需要 api_key：在 config.yaml 设 collector.tavily_api_key "
                "或环境变量 TAVILY_API_KEY"
            )
        self._keys = pool
        self._idx = 0

    def _search_with_key(self, key: str, query: str, max_results: int) -> list[SearchHit]:
        from tavily import TavilyClient  # noqa: PLC0415

        proxy = get_default_proxy()
        client = TavilyClient(
            api_key=key,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
        resp = client.search(query=query, max_results=max_results)
        hits: list[SearchHit] = []
        for r in resp.get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=r.get("title", ""),
                    snippet=r.get("content", ""),
                    source_engine=self.name,
                )
            )
        return hits

    def _search_sync(self, query: str, max_results: int) -> list[SearchHit]:
        from tavily import UsageLimitExceededError  # noqa: PLC0415

        last_err: Exception | None = None
        n = len(self._keys)
        offset = 0  # 已确认耗尽的 key 数（跳过）
        while offset < n:
            key = self._keys[(self._idx + offset) % n]
            try:
                hits = self._search_with_key(key, query, max_results)
                self._idx = (self._idx + offset) % n  # 记住可用 key 位置
                return hits
            except UsageLimitExceededError as e:
                last_err = e
                offset += 1  # 跳过此 key，后续调用从它后面开始
                continue
            except SearchError:
                raise
            except Exception as e:  # noqa: BLE001
                raise SearchError(f"Tavily 搜索失败: {e}") from e
        raise SearchError(f"Tavily 搜索失败: 所有 {n} 个 key 配额耗尽 ({last_err})")

    async def search(
        self, query: str, max_results: int, language: str = "both", **_kw
    ) -> list[SearchHit]:
        # **_kw 吸收 P2 的时间/排序/分页：Tavily 不支持，忽略
        try:
            return await asyncio.to_thread(self._search_sync, query, max_results)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"Tavily 搜索失败: {e}") from e
