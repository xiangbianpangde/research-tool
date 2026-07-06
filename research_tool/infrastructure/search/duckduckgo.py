"""DuckDuckGo 搜索后端（免费免 key）。

依据决策：搜索后端选用 DuckDuckGo + arxiv + Tavily。DDG 通过 ddgs 库访问，
无需 API Key，但有速率限制、偶尔不稳定。
"""

from __future__ import annotations

import asyncio

from ...domain.errors import SearchError
from .base import SearchBackend, SearchHit

# language → DDG region
_REGION = {"zh": "cn-zh", "en": "us-en", "both": "wt-wt"}


class DuckDuckGoBackend(SearchBackend):
    name = "web"

    def _search_sync(self, query: str, max_results: int, language: str) -> list[SearchHit]:
        try:
            from ddgs import DDGS
        except ImportError as e:  # pragma: no cover
            raise SearchError("需要 ddgs 包：pip install ddgs（或 research-tool[search]）") from e
        region = _REGION.get(language, "wt-wt")
        hits: list[SearchHit] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                hits.append(
                    SearchHit(
                        url=url,
                        title=r.get("title", ""),
                        snippet=r.get("body", ""),
                        source_engine=self.name,
                    )
                )
        return hits

    async def search(
        self, query: str, max_results: int, language: str = "both", **_kw
    ) -> list[SearchHit]:
        # **_kw 吸收 P2 的 from_year/to_year/sort/offset：web 搜索不支持，忽略
        try:
            return await asyncio.to_thread(self._search_sync, query, max_results, language)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"DuckDuckGo 搜索失败: {e}") from e
