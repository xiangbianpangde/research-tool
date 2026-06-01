"""Collector 修复回归：搜索 warnings、文件名防覆盖、sources.json 合并。"""

import pytest

from src.domain.errors import SearchError
from src.domain.models import CollectorConfig
from src.infrastructure.search.base import SearchHit
from src.infrastructure.stages.collector import Collector
from src.infrastructure.stages.fetcher import FetchResult, Fetcher

_LONG = "实质内容。" * 60  # > min_doc_chars


@pytest.mark.asyncio
async def test_search_warnings_not_silent(monkeypatch):
    cfg = CollectorConfig(search_engines=["web", "github"], search_cache=False)
    c = Collector(cfg)

    async def ddg_ok(self, q, n, lang, *, from_year=None, to_year=None, sort=None, offset=0):
        return [SearchHit(url="https://a.com", source_engine="web")]

    async def gh_boom(self, q, n, lang, *, from_year=None, to_year=None, sort=None, offset=0):
        raise SearchError("github 搜索失败: 429")

    monkeypatch.setattr(
        "src.infrastructure.search.duckduckgo.DuckDuckGoBackend.search", ddg_ok
    )
    monkeypatch.setattr(
        "src.infrastructure.search.github_backend.GitHubBackend.search", gh_boom
    )

    sr = await c.search_queries(["X"])
    assert len(sr.hits) == 1                       # web 的结果保留
    assert any("github" in w for w in sr.warnings)  # github 失败可观测，不再静默


@pytest.mark.asyncio
async def test_fetch_and_store_idempotent(monkeypatch, tmp_path):
    cfg = CollectorConfig(depth=2)
    c = Collector(cfg)
    monkeypatch.setattr(
        Fetcher, "fetch", lambda self, url: _ok(url)
    )
    raw = tmp_path / "raw"
    hit = SearchHit(url="https://x.org/a", title="A", source_engine="web")

    r1 = await c.fetch_and_store("t", [hit], raw)
    r2 = await c.fetch_and_store("t", [hit], raw)  # 同 URL 再来一次

    assert len(r1.files) == 1
    assert len(r2.files) == 0                       # 已采过 → 去重，不重复写
    assert len(list(raw.glob("*.md"))) == 1         # 没有产生第二份
    import json
    sources = json.loads((raw / "sources.json").read_text(encoding="utf-8"))
    assert len(sources) == 1                        # sources.json 也不重复


@pytest.mark.asyncio
async def test_fetch_and_store_no_overwrite_across_runs(monkeypatch, tmp_path):
    cfg = CollectorConfig(depth=2)
    c = Collector(cfg)
    monkeypatch.setattr(Fetcher, "fetch", lambda self, url: _ok(url))
    raw = tmp_path / "raw"

    await c.fetch_and_store("t", [SearchHit(url="https://x.org/a", source_engine="web")], raw)
    await c.fetch_and_store("t", [SearchHit(url="https://x.org/b", source_engine="web")], raw)

    files = sorted(p.name for p in raw.glob("*.md"))
    assert len(files) == 2                          # 第二轮新 URL 不覆盖第一轮
    assert files[0].startswith("01-") and files[1].startswith("02-")  # idx 续编


async def _ok(url):
    return FetchResult(url, _LONG, ok=True)
