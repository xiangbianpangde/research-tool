"""arxiv 搜索后端（免费官方 API）。

依据 07 §4 AutoSchemaKG/学术来源。返回 arxiv 摘要页 URL，供 Collector 抓取。
"""

from __future__ import annotations

import asyncio

from ..errors import SearchError
from .base import SearchBackend, SearchHit


class ArxivBackend(SearchBackend):
    name = "arxiv"

    def _search_sync(self, query: str, max_results: int) -> list[SearchHit]:
        try:
            import arxiv
        except ImportError as e:  # pragma: no cover
            raise SearchError(
                "需要 arxiv 包：pip install arxiv（或 research-tool[search]）"
            ) from e
        # page_size 贴合需求量，避免一次请求 100 条；加大重试与间隔以缓解 429
        client = arxiv.Client(
            page_size=min(max_results, 50), delay_seconds=3.0, num_retries=5
        )
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        hits: list[SearchHit] = []
        for r in client.results(search):
            hits.append(
                SearchHit(
                    url=r.entry_id,  # abs 页 URL
                    title=r.title,
                    snippet=(r.summary or "")[:500],
                    source_engine=self.name,
                )
            )
        return hits

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        from .cache import arxiv_throttle

        await arxiv_throttle()  # 进程级最小请求间隔，缓解 429
        try:
            return await asyncio.to_thread(self._search_sync, query, max_results)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"arxiv 搜索失败: {e}") from e
