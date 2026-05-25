"""Google News 搜索后端（RSS，免费无需 Key）。

提供最新进展/新闻。RSS feed 返回 XML，解析 item 的 title/link/description/pubDate。
link 是 Google 跳转链接，Fetcher（follow_redirects）会落到原文。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..errors import SearchError
from ._http import describe, get_text
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://news.google.com/rss/search"
_TAG = re.compile(r"<[^>]+>")


def _locale(language: str) -> dict:
    if language == "zh":
        return {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh"}
    return {"hl": "en-US", "gl": "US", "ceid": "US:en"}


class GoogleNewsBackend(SearchBackend):
    name = "google_news"

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        params = {"q": query, **_locale(language)}
        try:
            xml = await get_text(_ENDPOINT, params=params)
            root = ET.fromstring(xml)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"google_news 搜索失败: {describe(e)}") from e

        hits: list[SearchHit] = []
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            if not link:
                continue
            desc = _TAG.sub("", item.findtext("description") or "").strip()
            date = (item.findtext("pubDate") or "").strip()
            snippet = " — ".join(p for p in (date, desc) if p)
            hits.append(
                SearchHit(
                    url=link,
                    title=(item.findtext("title") or "").strip(),
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
            if len(hits) >= max_results:
                break
        return hits
