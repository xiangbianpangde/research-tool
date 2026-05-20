"""搜索后端工厂。"""

from __future__ import annotations

from ..models import CollectorConfig
from .base import SearchBackend, SearchHit


def get_backend(name: str, config: CollectorConfig) -> SearchBackend:
    """按引擎名构造后端。'scholar' 暂映射到 arxiv。"""
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


__all__ = ["SearchBackend", "SearchHit", "get_backend"]
