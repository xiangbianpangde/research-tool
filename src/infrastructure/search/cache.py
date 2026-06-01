"""搜索结果磁盘缓存 + arxiv 全局限速。

缓解 arxiv 等后端的 429 限流：相同查询命中缓存直接返回；arxiv 请求间施加
进程级最小间隔与指数退避。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from .base import SearchBackend, SearchHit


def _default_cache_dir() -> Path:
    return Path.home() / ".research" / "cache" / "search"


class SearchCache:
    def __init__(self, cache_dir: str | Path | None = None, ttl_sec: int = 86400) -> None:
        self.dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self.ttl_sec = ttl_sec
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, engine: str, query: str, variant: str) -> Path:
        # variant 编码 max_results|language|from_year|to_year|sort|offset，
        # 必须纳入键：否则 deep-search 多排序/多页/不同年份窗口会命中同一缓存（致命）。
        raw = f"{engine}|{query}|{variant}"
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return self.dir / f"{engine}-{key}.json"

    @staticmethod
    def _variant(
        max_results: int, language: str,
        from_year: int | None, to_year: int | None,
        sort: str | None, offset: int,
    ) -> str:
        return f"{max_results}|{language}|{from_year}|{to_year}|{sort}|{offset}"

    def get(
        self, engine: str, query: str, variant: str
    ) -> list[SearchHit] | None:
        path = self._path(engine, query, variant)
        if not path.exists():
            return None
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if self.ttl_sec and time.time() - blob.get("created", 0) > self.ttl_sec:
            return None
        return [SearchHit(**h) for h in blob.get("hits", [])]

    def set(
        self, engine: str, query: str, variant: str, hits: list[SearchHit],
    ) -> None:
        path = self._path(engine, query, variant)
        blob = {"created": time.time(), "hits": [h.model_dump() for h in hits]}
        path.write_text(
            json.dumps(blob, ensure_ascii=False, indent=1), encoding="utf-8"
        )


class CachingBackend(SearchBackend):
    """装饰任意后端：先查缓存，未命中再调用内层并回写。"""

    def __init__(self, inner: SearchBackend, cache: SearchCache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name

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
        variant = self.cache._variant(
            max_results, language, from_year, to_year, sort, offset
        )
        cached = self.cache.get(self.name, query, variant)
        if cached is not None:
            return cached
        hits = await self.inner.search(
            query, max_results, language,
            from_year=from_year, to_year=to_year, sort=sort, offset=offset,
        )
        if hits:
            self.cache.set(self.name, query, variant, hits)
        return hits


# --- arxiv 全局限速（进程级，约 1 请求 / min_interval 秒）-------------------- #
_arxiv_lock = asyncio.Lock()
_arxiv_last = 0.0


async def arxiv_throttle(min_interval: float = 3.0) -> None:
    """在 arxiv 请求前调用：保证两次请求间隔 >= min_interval 秒。"""
    global _arxiv_last
    async with _arxiv_lock:
        wait = min_interval - (time.monotonic() - _arxiv_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _arxiv_last = time.monotonic()
