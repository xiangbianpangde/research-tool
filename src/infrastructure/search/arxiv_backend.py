"""arxiv 搜索后端（免费官方 API）。

依据 07 §4 AutoSchemaKG/学术来源。返回 arxiv 摘要页 URL，供 Collector 抓取。
"""

from __future__ import annotations

import asyncio

from ...domain.errors import SearchError
from .base import SearchBackend, SearchHit


class ArxivBackend(SearchBackend):
    name = "arxiv"

    def _search_sync(
        self, query: str, max_results: int,
        from_year: int | None, to_year: int | None,
        sort: str | None, offset: int,
    ) -> list[SearchHit]:
        try:
            import arxiv
        except ImportError as e:  # pragma: no cover
            raise SearchError(
                "需要 arxiv 包：pip install arxiv（或 research-tool[search]）"
            ) from e
        # arxiv 无 citations 排序：date→SubmittedDate，其余→Relevance
        criterion = (
            arxiv.SortCriterion.SubmittedDate if sort == "date"
            else arxiv.SortCriterion.Relevance
        )
        # 年份过滤靠客户端：多取一些再筛，避免过滤后不足量
        want = max_results + (offset or 0)
        fetch = want * 2 if (from_year or to_year) else want
        client = arxiv.Client(
            page_size=min(fetch, 100), delay_seconds=3.0, num_retries=5
        )
        search = arxiv.Search(
            query=query, max_results=fetch, sort_by=criterion,
        )
        hits: list[SearchHit] = []
        for r in client.results(search):
            year = getattr(r, "published", None)
            year = year.year if year else None
            if from_year is not None and (year is None or year < from_year):
                continue
            if to_year is not None and (year is None or year > to_year):
                continue
            hits.append(
                SearchHit(
                    url=r.entry_id,  # abs 页 URL
                    title=r.title,
                    snippet=(r.summary or "")[:500],
                    source_engine=self.name,
                )
            )
        # 客户端分页：跳过 offset 条，再取 max_results 条
        return hits[offset: offset + max_results] if offset else hits[:max_results]

    async def search(
        self, query: str, max_results: int, language: str = "both",
        *, from_year: int | None = None, to_year: int | None = None,
        sort: str | None = None, offset: int = 0,
    ) -> list[SearchHit]:
        from .cache import arxiv_throttle

        await arxiv_throttle()  # 进程级最小请求间隔，缓解 429
        try:
            return await asyncio.to_thread(
                self._search_sync, query, max_results,
                from_year, to_year, sort, offset,
            )
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"arxiv 搜索失败: {e}") from e
