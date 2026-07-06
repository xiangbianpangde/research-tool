"""arXiv search backend using the official Atom API."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from ...domain.errors import SearchError
from ._http import describe, get_text
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom"}


class ArxivBackend(SearchBackend):
    name = "arxiv"

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
        sort_by = "submittedDate" if sort == "date" else "relevance"
        params = {
            "search_query": f"all:{query}",
            "start": max(offset, 0),
            "max_results": min(max_results * 3 if (from_year or to_year) else max_results, 50),
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        try:
            text = await get_text(_ENDPOINT, params=params, timeout=25.0, retries=2)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429:
                raise SearchError("arxiv 官方 API 限流 429；请稍后重试或减少并发/查询频率") from e
            raise SearchError(f"arxiv 搜索失败: {describe(e)}") from e
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"arxiv 搜索失败: {describe(e)}") from e

        try:
            root = ET.fromstring(text)  # noqa: S314  # parsing arxiv Atom feed; defusedxml migration is a follow-up
        except ET.ParseError as e:
            raise SearchError(f"arxiv 响应 XML 解析失败: {e}") from e

        hits: list[SearchHit] = []
        for entry in root.findall("a:entry", _NS):
            title = " ".join((entry.findtext("a:title", default="", namespaces=_NS) or "").split())
            summary = " ".join(
                (entry.findtext("a:summary", default="", namespaces=_NS) or "").split()
            )
            url = entry.findtext("a:id", default="", namespaces=_NS) or ""
            published = entry.findtext("a:published", default="", namespaces=_NS) or ""
            year = int(published[:4]) if published[:4].isdigit() else None
            if from_year is not None and (year is None or year < from_year):
                continue
            if to_year is not None and (year is None or year > to_year):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=f"{year or ''}\n{summary[:500]}".strip(),
                    source_engine=self.name,
                )
            )
            if len(hits) >= max_results:
                break
        return hits
