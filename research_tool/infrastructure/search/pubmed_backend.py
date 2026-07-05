"""PubMed 搜索后端（NCBI E-utilities，免费无需 Key）。

覆盖 3700 万+ 生物医学文献。两步：
  esearch  → 拿 PMID 列表
  esummary → 拿每篇标题/作者/期刊/年份
正文由 Fetcher 抓 pubmed 摘要页。无 Key 时约 3 请求/秒，已在 _http 做退避。
"""

from __future__ import annotations

from ...domain.errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubMedBackend(SearchBackend):
    name = "pubmed"

    async def search(
        self, query: str, max_results: int, language: str = "both",
        *, from_year: int | None = None, to_year: int | None = None,
        sort: str | None = None, offset: int = 0,
    ) -> list[SearchHit]:
        try:
            ids = await self._esearch(
                query, min(max_results, 20),
                from_year=from_year, to_year=to_year, sort=sort, offset=offset,
            )
            if not ids:
                return []
            return await self._esummary(ids)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"pubmed 搜索失败: {describe(e)}") from e

    async def _esearch(
        self, query: str, retmax: int,
        *, from_year: int | None = None, to_year: int | None = None,
        sort: str | None = None, offset: int = 0,
    ) -> list[str]:
        params = {
            "db": "pubmed", "term": query,
            "retmax": retmax, "retmode": "json",
        }
        # 年份过滤（按出版日 pdat）
        if from_year is not None or to_year is not None:
            params["datetype"] = "pdat"
            params["mindate"] = str(from_year) if from_year is not None else "1500"
            params["maxdate"] = str(to_year) if to_year is not None else "3000"
        if sort == "date":           # PubMed 仅支持按日期；citations 不支持
            params["sort"] = "pub_date"
        if offset:
            params["retstart"] = offset
        data = await get_json(_ESEARCH, params=params)
        result = data.get("esearchresult", {}) if isinstance(data, dict) else {}
        return list(result.get("idlist", []))

    async def _esummary(self, ids: list[str]) -> list[SearchHit]:
        data = await get_json(
            _ESUMMARY,
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        )
        result = data.get("result", {}) if isinstance(data, dict) else {}
        hits: list[SearchHit] = []
        for pmid in result.get("uids", []):
            doc = result.get(pmid, {})
            authors = ", ".join(
                a.get("name", "") for a in (doc.get("authors") or [])[:4]
            )
            journal = doc.get("fulljournalname") or doc.get("source") or ""
            date = doc.get("pubdate") or ""
            meta = " | ".join(p for p in (date, journal, authors) if p)
            hits.append(
                SearchHit(
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    title=doc.get("title", ""),
                    snippet=meta,
                    source_engine=self.name,
                )
            )
        return hits
