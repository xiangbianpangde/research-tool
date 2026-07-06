import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search import get_backend
from research_tool.infrastructure.search.base import SearchBackend, SearchHit
from research_tool.infrastructure.search.cache import CachingBackend, SearchCache

_V = "5|en|None|None|None|0"


def test_cache_roundtrip(tmp_path):
    cache = SearchCache(tmp_path, ttl_sec=1000)
    hits = [SearchHit(url="https://a.com", title="A", source_engine="web")]
    assert cache.get("web", "q", _V) is None
    cache.set("web", "q", _V, hits)
    got = cache.get("web", "q", _V)
    assert got and got[0].url == "https://a.com"


def test_cache_ttl_expiry(tmp_path):
    cache = SearchCache(tmp_path, ttl_sec=-1)
    cache.set("web", "q", _V, [SearchHit(url="https://a.com")])
    assert cache.get("web", "q", _V) is None


class _Counter(SearchBackend):
    name = "web"

    def __init__(self):
        self.calls = 0

    async def search(
        self,
        query,
        max_results,
        language="both",
        *,
        from_year=None,
        to_year=None,
        sort=None,
        offset=0,
    ):
        self.calls += 1
        return [SearchHit(url="https://x.com", source_engine="web")]


@pytest.mark.asyncio
async def test_caching_backend_avoids_second_call(tmp_path):
    inner = _Counter()
    wrapped = CachingBackend(inner, SearchCache(tmp_path))
    await wrapped.search("q", 3, "en")
    await wrapped.search("q", 3, "en")
    assert inner.calls == 1  # 第二次命中缓存


def test_get_backend_wraps_when_enabled(tmp_path):
    cfg = CollectorConfig(search_cache=True, cache_dir=str(tmp_path))
    b = get_backend("web", cfg)
    assert isinstance(b, CachingBackend)

    cfg2 = CollectorConfig(search_cache=False)
    assert not isinstance(get_backend("web", cfg2), CachingBackend)
