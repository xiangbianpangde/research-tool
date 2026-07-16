"""CVPR 论文搜索后端（DBLP API）。

走 DBLP 公开 JSON API（无需 key），用 ``venue:CVPR`` 做 venue 原生过滤，
避免 OpenAlex 长 venue 名匹配噪声，也避开 CVF Open Access HTML 正则脆弱性
（真实页面 ``dt.ptitle`` 内含 ``<br>``，基于 2024 结构的裸正则曾 0 命中）。

- ``from_year`` / ``to_year`` 映射为 DBLP ``year:`` 查询；均不设时不限年份。
- ``query`` 并入关键词；空 query 时仅按 venue(+year) 拉最近命中。
- ``ee`` 字段作 url（常指向 openaccess.thecvf.com 或 arxiv）。

API: https://dblp.org/search/publ/api?q=...&format=json&h=N
"""

from __future__ import annotations

import re

import httpx

from ...domain.errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://dblp.org/search/publ/api"
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _as_list(val) -> list:
    """DBLP 单元素字段常为标量，多元素为 list。"""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _first_ee(info: dict) -> str:
    ees = _as_list(info.get("ee"))
    for ee in ees:
        if isinstance(ee, dict):
            # 部分响应 ee 为 {"@type": "...", "text": "url"}
            url = ee.get("text") or ee.get("#text") or ""
            if url:
                return str(url)
        elif ee:
            return str(ee)
    return ""


def _authors_str(info: dict) -> str:
    authors = info.get("authors") or {}
    if isinstance(authors, dict):
        items = _as_list(authors.get("author"))
    else:
        items = _as_list(authors)
    names: list[str] = []
    for a in items:
        if isinstance(a, dict):
            n = a.get("text") or a.get("#text") or a.get("@pid") or ""
            if n:
                names.append(str(n))
        elif a:
            names.append(str(a))
    return ", ".join(names)


class CvprBackend(SearchBackend):
    """DBLP 驱动的 CVPR venue 限定论文源。引擎名 ``cvpr``。"""

    name = "cvpr"

    def _build_query(
        self,
        query: str,
        from_year: int | None,
        to_year: int | None,
    ) -> str:
        """组装 DBLP 查询串。

        不用 ``venue:CVPR`` 前缀：DBLP 对该 filter 与自由词组合偶发 ``Unknown Error``
        且 0 命中。改以关键词 + ``CVPR`` 文本检索，venue 严格过滤放在客户端
        （``"CVPR" in venue``）。年份单值时附加 ``year:YYYY``。
        """
        parts: list[str] = []
        q = (query or "").strip()
        # 去掉用户自带的 venue: 以免干扰
        if q.lower().startswith("venue:"):
            q = q.split(None, 1)[1] if " " in q else ""
        if q:
            parts.append(q)
        # 无关键词时用 CVPR 作锚，避免空 q 全库扫
        if not parts:
            parts.append("CVPR")
        elif "cvpr" not in q.lower():
            parts.append("CVPR")
        if from_year is not None and to_year is not None and from_year == to_year:
            parts.append(f"year:{from_year}")
        elif from_year is not None and to_year is None:
            parts.append(f"year:{from_year}")
        elif to_year is not None and from_year is None:
            parts.append(f"year:{to_year}")
        return " ".join(parts)

    def _year_ok(self, year: int | None, from_year: int | None, to_year: int | None) -> bool:
        if year is None:
            return from_year is None and to_year is None
        if from_year is not None and year < from_year:
            return False
        if to_year is not None and year > to_year:
            return False
        return True

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
        **_kw,
    ) -> list[SearchHit]:
        # 多拉一些供跨年客户端过滤与词重叠精筛
        fetch_n = min(max(max_results * 3, max_results + offset), 100)
        q = self._build_query(query, from_year, to_year)
        params = {
            "q": q,
            "format": "json",
            "h": fetch_n,
            "f": max(offset, 0),
        }
        try:
            data = await get_json(_ENDPOINT, params=params, timeout=25.0, retries=2)
        except httpx.HTTPStatusError as e:
            raise SearchError(f"cvpr(DBLP) 搜索失败: {describe(e)}") from e
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"cvpr(DBLP) 搜索失败: {describe(e)}") from e

        result = (data.get("result") or {}) if isinstance(data, dict) else {}
        hits_block = result.get("hits") or {}
        raw_hits = _as_list(hits_block.get("hit") if isinstance(hits_block, dict) else hits_block)

        q_tokens = _tokens(query)
        out: list[SearchHit] = []
        for item in raw_hits:
            if not isinstance(item, dict):
                continue
            info = item.get("info") or item
            if not isinstance(info, dict):
                continue
            venue = str(info.get("venue") or "")
            # 二次 venue 闸：含 CVPR 即收（主会 + workshop 命名变体）
            if "CVPR" not in venue.upper():
                continue
            year_raw = info.get("year")
            try:
                year = int(year_raw) if year_raw is not None else None
            except (TypeError, ValueError):
                year = None
            if not self._year_ok(year, from_year, to_year):
                continue

            title = str(info.get("title") or "").strip()
            authors = _authors_str(info)
            # 用户关键词：title+authors 词重叠；空 query 全收
            if q_tokens and not (q_tokens & _tokens(f"{title} {authors}")):
                continue

            url = _first_ee(info) or str(info.get("url") or "")
            if not url and info.get("key"):
                # 回退到 DBLP 记录页
                url = f"https://dblp.org/rec/{info['key']}"
            if not url:
                continue

            snippet_parts = []
            if year is not None:
                snippet_parts.append(str(year))
            if venue:
                snippet_parts.append(venue)
            if authors:
                snippet_parts.append(authors[:300])
            out.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet="\n".join(snippet_parts),
                    source_engine=self.name,
                )
            )
            if len(out) >= max_results:
                break
        return out
