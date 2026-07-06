"""Semantic Scholar 搜索后端（免费 Graph API）。

覆盖全出版商的学术论文（不限 arXiv），含引用数等元数据。
API: https://api.semanticscholar.org/graph/v1/paper/search

无 Key 时共享一个限流池，容易 429——已在 _http.get_json 里做指数退避；
配置 semantic_scholar_api_key 可显著提升配额（x-api-key 头）。
"""

from __future__ import annotations

import httpx

from ...domain.errors import SearchError
from ._http import describe, get_json
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
        self,
        query: str,
        max_results: int,
        language: str = "both",
        *,
        from_year: int | None = None,
        to_year: int | None = None,
        sort: str | None = None,
        offset: int = 0,
    ) -> list[SearchHit]:
        headers = {"x-api-key": self.api_key} if self.api_key else None
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": _FIELDS,
        }
        # 年份过滤：S2 /paper/search 原生支持 year=2016-2020 / 2016- / -2020
        if from_year is not None or to_year is not None:
            params["year"] = f"{from_year or ''}-{to_year or ''}"
        if offset:
            params["offset"] = offset
        try:
            data = await get_json(_ENDPOINT, params=params, headers=headers)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429 and not self.api_key:
                raise SearchError(
                    "semantic_scholar 触发 429 限流；请配置 "
                    "collector.semantic_scholar_api_key 或稍后重试"
                ) from e
            raise SearchError(f"semantic_scholar 搜索失败: {describe(e)}") from e
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"semantic_scholar 搜索失败: {describe(e)}") from e

        papers = (data.get("data") or []) if isinstance(data, dict) else []
        # /paper/search 不支持服务端 sort，按当前页客户端重排（best-effort，
        # deep-search 的真正翻页多样性靠 offset + openalex/arxiv 的原生排序）
        if sort == "date":
            papers = sorted(papers, key=lambda p: p.get("year") or 0, reverse=True)
        elif sort == "citations":
            papers = sorted(papers, key=lambda p: p.get("citationCount") or 0, reverse=True)

        hits: list[SearchHit] = []
        for paper in papers:
            url = self._best_url(paper)
            if not url:
                continue
            authors = ", ".join(a.get("name", "") for a in (paper.get("authors") or [])[:5])
            cites = paper.get("citationCount")
            year = paper.get("year")
            meta = " | ".join(
                p
                for p in (
                    f"{year}" if year else "",
                    authors,
                    f"被引 {cites}" if cites is not None else "",
                )
                if p
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
