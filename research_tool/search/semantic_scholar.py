"""Semantic Scholar 搜索后端（免费 Graph API）。

覆盖全出版商的学术论文（不限 arXiv），含引用数等元数据。
API: https://api.semanticscholar.org/graph/v1/paper/search

无 Key 时共享一个限流池，容易 429——已在 _http.get_json 里做指数退避；
配置 semantic_scholar_api_key 可显著提升配额（x-api-key 头）。
"""

from __future__ import annotations

from ..errors import SearchError
from ._http import get_json
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,year,authors,citationCount,url,externalIds,openAccessPdf"


class SemanticScholarBackend(SearchBackend):
    name = "semantic_scholar"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def _best_url(self, paper: dict) -> str:
        """优先可抓全文的链接：开放获取 PDF > arXiv abs > DOI > S2 页面。"""
        oa = paper.get("openAccessPdf") or {}
        if oa.get("url"):
            return oa["url"]
        ext = paper.get("externalIds") or {}
        if ext.get("ArXiv"):
            return f"https://arxiv.org/abs/{ext['ArXiv']}"
        if ext.get("DOI"):
            return f"https://doi.org/{ext['DOI']}"
        return paper.get("url") or ""

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        headers = {"x-api-key": self.api_key} if self.api_key else None
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": _FIELDS,
        }
        try:
            data = await get_json(_ENDPOINT, params=params, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"semantic_scholar 搜索失败: {e}") from e

        hits: list[SearchHit] = []
        for paper in (data.get("data") or []) if isinstance(data, dict) else []:
            url = self._best_url(paper)
            if not url:
                continue
            authors = ", ".join(
                a.get("name", "") for a in (paper.get("authors") or [])[:5]
            )
            cites = paper.get("citationCount")
            year = paper.get("year")
            meta = " | ".join(
                p for p in (
                    f"{year}" if year else "",
                    authors,
                    f"被引 {cites}" if cites is not None else "",
                ) if p
            )
            abstract = (paper.get("abstract") or "")[:500]
            snippet = f"{meta}\n{abstract}".strip() if meta else abstract
            hits.append(
                SearchHit(
                    url=url,
                    title=paper.get("title", ""),
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
        return hits
