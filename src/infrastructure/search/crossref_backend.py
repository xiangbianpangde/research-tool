"""Crossref 搜索后端（免费无需 Key，1.5 亿+ DOI 记录）。

跨出版商的学术元数据库。按相关性排序——Crossref 的 published 日期字段有
不少脏数据（出现 2100/2115 等未来年份），故不依赖日期排序。
配置 collector.crossref_mailto 进 polite pool 更稳。
"""

from __future__ import annotations

from ...domain.errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://api.crossref.org/works"


def _year(item: dict) -> str:
    for key in ("published", "published-online", "published-print", "issued"):
        parts = (item.get(key) or {}).get("date-parts") or [[None]]
        y = parts[0][0] if parts and parts[0] else None
        # 过滤明显脏数据（未来年份）
        if isinstance(y, int) and 1500 <= y <= 2100:
            return str(y)
    return ""


class CrossrefBackend(SearchBackend):
    name = "crossref"

    def __init__(self, mailto: str | None = None) -> None:
        self.mailto = mailto

    # 抽象 sort 键 → Crossref (sort, order)。日期脏数据多，date 谨慎用。
    _SORTS = {
        "date": ("published", "desc"),
        "citations": ("is-referenced-by-count", "desc"),
    }

    async def search(
        self, query: str, max_results: int, language: str = "both",
        *, from_year: int | None = None, to_year: int | None = None,
        sort: str | None = None, offset: int = 0,
    ) -> list[SearchHit]:
        params = {"query": query, "rows": min(max_results, 30)}
        if self.mailto:
            params["mailto"] = self.mailto
        # 年份过滤（含起止）
        filters = []
        if from_year is not None:
            filters.append(f"from-pub-date:{from_year}-01-01")
        if to_year is not None:
            filters.append(f"until-pub-date:{to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if offset:
            params["offset"] = offset
        if sort and sort in self._SORTS:
            params["sort"], params["order"] = self._SORTS[sort]
        try:
            data = await get_json(_ENDPOINT, params=params)
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"crossref 搜索失败: {describe(e)}") from e

        items = (
            data.get("message", {}).get("items", [])
            if isinstance(data, dict) else []
        )
        hits: list[SearchHit] = []
        for item in items:
            url = item.get("URL") or (
                f"https://doi.org/{item['DOI']}" if item.get("DOI") else ""
            )
            if not url:
                continue
            title = (item.get("title") or [""])[0]
            authors = ", ".join(
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (item.get("author") or [])[:5]
            )
            journal = (item.get("container-title") or [""])[0]
            meta = " | ".join(p for p in (_year(item), journal, authors) if p)
            hits.append(
                SearchHit(
                    url=url, title=title, snippet=meta, source_engine=self.name
                )
            )
            if len(hits) >= max_results:
                break
        return hits
