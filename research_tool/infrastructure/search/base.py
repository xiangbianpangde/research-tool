"""搜索后端抽象。

Collector 的搜索能力（设计 01/02/04 中的 search_engine: web/arxiv/tavily）
被抽象为 SearchBackend，各后端只负责"给查询返回候选 URL 列表"，抓取交给
Collector 的 Fetcher。
"""

from __future__ import annotations

import abc

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """单条搜索命中。"""

    url: str
    title: str = ""
    snippet: str = ""
    source_engine: str = ""


class SearchResult(BaseModel):
    """一次多引擎×多查询搜索的聚合结果。

    携带 warnings 让搜索后端的失败可观测（修复 1），不再静默丢弃。
    """

    hits: list[SearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SearchBackend(abc.ABC):
    """搜索后端接口。实现需为同步逻辑包一层 async（用 asyncio.to_thread）。

    P2 扩展（时间标签 / deep-search）：新增 from_year/to_year/sort/offset，
    均为可选关键字参数，默认值等价于旧行为，老调用方无需改动。
    各后端按能力支持：原生过滤（openalex/s2/crossref/pubmed）、客户端过滤
    （arxiv 年份）、或忽略（web/wikipedia）。
    """

    name: str = "base"

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        *,
        from_year: int | None = None,  # 起始年（含），None=不限
        to_year: int | None = None,  # 截止年（含），None=不限
        sort: str | None = None,  # 排序键："relevance"|"date"|"citations"，None=后端默认
        offset: int = 0,  # 分页偏移（deep-search 翻页）
    ) -> list[SearchHit]:
        """返回至多 max_results 条命中。"""
