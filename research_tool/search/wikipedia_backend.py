"""Wikipedia 搜索后端（免费 REST API，无需 Key）。

提供百科背景知识。统一走「搜索返回页面 URL + 摘要，由 Fetcher 抓全文」模式
（内联模式已废弃：SearchHit 无 inline_content 字段，见问题清单问题 3）。

对人物调研尤其有用：搜 "Yilin Kang"（不挂机构名）可命中独立个人词条，
补足官网之外的生平/教育背景信息（康怡琳偏差案例）。
"""

from __future__ import annotations

import re

from ..errors import SearchError
from ._http import get_json
from .base import SearchBackend, SearchHit

# rest.php 搜索摘要里高亮命中词用 <span class="searchmatch">，需去掉
_TAG = re.compile(r"<[^>]+>")


def _endpoint(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page"


def _wiki_langs(language: str) -> list[str]:
    """both → 中英文双站点都搜（人物/概念跨语种覆盖）。"""
    if language == "zh":
        return ["zh"]
    if language == "en":
        return ["en"]
    return ["en", "zh"]


class WikipediaBackend(SearchBackend):
    name = "wikipedia"

    async def _search_lang(
        self, lang: str, query: str, limit: int
    ) -> list[SearchHit]:
        params = {"q": query, "limit": min(limit, 50)}
        data = await get_json(_endpoint(lang), params=params)
        pages = (data.get("pages") or []) if isinstance(data, dict) else []
        hits: list[SearchHit] = []
        for page in pages:
            key = page.get("key") or page.get("title", "").replace(" ", "_")
            url = f"https://{lang}.wikipedia.org/wiki/{key}"
            excerpt = _TAG.sub("", page.get("excerpt") or "")
            desc = page.get("description") or ""
            snippet = " — ".join(p for p in (desc, excerpt) if p)
            hits.append(
                SearchHit(
                    url=url,
                    title=page.get("title", ""),
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
        return hits

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        seen: set[str] = set()
        try:
            for lang in _wiki_langs(language):
                for hit in await self._search_lang(lang, query, max_results):
                    if hit.url in seen:
                        continue
                    seen.add(hit.url)
                    hits.append(hit)
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"wikipedia 搜索失败: {e}") from e
        return hits[:max_results]
