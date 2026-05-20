"""搜索后端工厂。"""

from __future__ import annotations

from ..models import CollectorConfig
from .base import SearchBackend, SearchHit


def _build_inner(name: str, config: CollectorConfig) -> SearchBackend:
    if name == "web":
        from .duckduckgo import DuckDuckGoBackend

        return DuckDuckGoBackend()
    if name in ("arxiv", "scholar"):
        from .arxiv_backend import ArxivBackend

        return ArxivBackend()
    if name == "tavily":
        from .tavily import TavilyBackend

        return TavilyBackend(config.tavily_api_key)
    raise ValueError(f"未知搜索引擎: {name}")


def get_backend(name: str, config: CollectorConfig) -> SearchBackend:
    """按引擎名构造后端，开启缓存时套一层 CachingBackend。'scholar' 暂映射到 arxiv。"""
    inner = _build_inner(name, config)
    if config.search_cache:
        from .cache import CachingBackend, SearchCache

        cache = SearchCache(config.cache_dir, ttl_sec=config.cache_ttl_sec)
        return CachingBackend(inner, cache)
    return inner


__all__ = ["SearchBackend", "SearchHit", "get_backend"]
