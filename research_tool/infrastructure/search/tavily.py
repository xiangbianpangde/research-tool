"""Tavily 搜索后端（需 API Key，稳定高质量）。

依据决策：搜索后端含 Tavily。Key 来自 CollectorConfig.tavily_api_key
（可由 ${TAVILY_API_KEY} 注入）。
"""

from __future__ import annotations

import asyncio

from ...domain.errors import SearchError
from ._http import get_default_proxy
from .base import SearchBackend, SearchHit


class TavilyBackend(SearchBackend):
    name = "tavily"

    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise SearchError(
                "Tavily 后端需要 api_key：在 config.yaml 设 collector.tavily_api_key "
                "或环境变量 TAVILY_API_KEY"
            )
        self._api_key = api_key

    def _search_sync(self, query: str, max_results: int) -> list[SearchHit]:
        try:
            from tavily import TavilyClient
        except ImportError as e:  # pragma: no cover
            raise SearchError(
                "需要 tavily-python 包：pip install tavily-python（或 research-tool[search]）"
            ) from e
        proxy = get_default_proxy()
        client = TavilyClient(
            api_key=self._api_key,
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
