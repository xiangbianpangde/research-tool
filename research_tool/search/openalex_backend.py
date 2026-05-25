"""OpenAlex 搜索后端（免费无需 Key，2.5 亿+ 学术作品）。

OpenAlex 覆盖面广、限流宽松、元数据干净，是 arxiv/semantic_scholar 的强力补充。
针对"搜不到近期论文"的痛点（arxiv/S2 默认按相关性排序，偏向高引老论文），
本后端做**混合检索**：一半按相关性、一半按发表日期倒序，再合并去重，
既覆盖经典又覆盖最新。

配置 collector.openalex_mailto 可进 polite pool（限流更宽、更稳）。
"""

from __future__ import annotations

from ..errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://api.openalex.org/works"


def _reconstruct_abstract(inv: dict | None) -> str:
    """OpenAlex 的 abstract 以倒排索引给出 {word: [positions]}，按位置还原成文本。"""
    if not inv:
        return ""
    positioned = sorted((pos, w) for w, ps in inv.items() for pos in ps)
    return " ".join(w for _, w in positioned)


class OpenAlexBackend(SearchBackend):
    name = "openalex"

    def __init__(self, mailto: str | None = None) -> None:
        self.mailto = mailto

    async def _query(self, query: str, n: int, sort: str | None) -> list[dict]:
        params: dict = {"search": query, "per-page": min(max(n, 1), 50)}
        if sort:
            params["sort"] = sort
        if self.mailto:
            params["mailto"] = self.mailto
        data = await get_json(_ENDPOINT, params=params)
        return data.get("results", []) if isinstance(data, dict) else []

    def _best_url(self, work: dict) -> str:
        for key in ("best_oa_location", "primary_location"):
            loc = work.get(key) or {}
            if loc.get("pdf_url"):
                return loc["pdf_url"]
            if loc.get("landing_page_url"):
                return loc["landing_page_url"]
        if work.get("doi"):
            return work["doi"]  # 形如 https://doi.org/...
        return work.get("id", "")  # openalex id 也是可访问 URL

    def _to_hit(self, work: dict) -> SearchHit | None:
        url = self._best_url(work)
        if not url:
            return None
        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in (work.get("authorships") or [])[:5]
        )
        year = work.get("publication_year")
        cites = work.get("cited_by_count")
        meta = " | ".join(
            p for p in (
                str(year) if year else "",
                authors,
                f"被引 {cites}" if cites is not None else "",
            ) if p
        )
        abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))[:500]
        snippet = f"{meta}\n{abstract}".strip() if meta else abstract
        return SearchHit(
            url=url, title=work.get("title") or "",
            snippet=snippet, source_engine=self.name,
        )

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        try:
            half = max(1, max_results // 2)
            relevant = await self._query(query, half, sort=None)  # 默认相关性
            recent = await self._query(
                query, max_results - half, sort="publication_date:desc"
            )
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"openalex 搜索失败: {describe(e)}") from e

        hits: list[SearchHit] = []
        seen: set[str] = set()
        # 交替合并：相关性优先呈现，但保证最新批次也进入结果
        for work in relevant + recent:
            hit = self._to_hit(work)
            if hit is None or hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)
            if len(hits) >= max_results:
                break
        return hits
