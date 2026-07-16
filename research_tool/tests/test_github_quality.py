"""GitHub 发现质量 P0/P1：查询变体、双路候选、专家弱档、raw README。"""

from __future__ import annotations

import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search.github_backend import (
    GitHubBackend,
    query_variants,
)
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import Fetcher


def test_query_variants_short_and_full():
    vs = query_variants("vggt 3d reconstruction geometry")
    assert vs[0] == "vggt 3d reconstruction geometry"
    # 产品缩写单独出现；泛词 3d 不得单独成 query
    assert "vggt" in vs
    assert "3d" not in vs
    assert len(vs) >= 2
    assert len(vs) <= 4


@pytest.mark.asyncio
async def test_github_dual_sort_and_variants_merge(monkeypatch):
    """best match + updated × 多 query 合并后应能保留权威仓。"""
    calls: list[dict] = []

    async def fake(url, **kw):
        params = kw.get("params") or {}
        calls.append(dict(params))
        q = params.get("q", "")
        sort = params.get("sort")
        # 全词 query 只返回边缘仓；短 query vggt 返回官方仓
        if q == "vggt" or (isinstance(q, str) and q.strip() == "vggt"):
            items = [
                {
                    "full_name": "facebookresearch/vggt",
                    "html_url": "https://github.com/facebookresearch/vggt",
                    "description": "VGGT visual geometry grounded transformer",
                    "stargazers_count": 13000,
                    "forks_count": 1000,
                    "language": "Python",
                    "pushed_at": "2026-05-19T00:00:00Z",
                    "topics": ["3d", "vision"],
                    "homepage": "https://example.com",
                },
                {
                    "full_name": "facebookresearch/vggt-omega",
                    "html_url": "https://github.com/facebookresearch/vggt-omega",
                    "description": "VGGT Omega CVPR oral",
                    "stargazers_count": 3400,
                    "forks_count": 180,
                    "language": "Python",
                    "pushed_at": "2026-07-02T00:00:00Z",
                    "topics": ["vggt"],
                },
            ]
        else:
            items = [
                {
                    "full_name": "other/noise",
                    "html_url": "https://github.com/other/noise",
                    "description": "unrelated",
                    "stargazers_count": 1,
                    "forks_count": 0,
                    "language": "Python",
                    "pushed_at": "2026-06-01T00:00:00Z",
                }
            ]
        # updated 路再塞一个新仓
        if sort == "updated":
            items = items + [
                {
                    "full_name": "newbie/vggt-fork",
                    "html_url": "https://github.com/newbie/vggt-fork",
                    "description": "fresh fork",
                    "stargazers_count": 2,
                    "forks_count": 0,
                    "pushed_at": "2026-07-10T00:00:00Z",
                }
            ]
        return {"items": items}

    monkeypatch.setattr(
        "research_tool.infrastructure.search.github_backend.get_json", fake
    )
    hits = await GitHubBackend(token=None, enable_code_search=False).search(
        "vggt 3d reconstruction geometry", 10
    )
    names = [h.title for h in hits]
    assert "facebookresearch/vggt" in names
    assert "facebookresearch/vggt-omega" in names
    # 至少打了 best match 与 updated 两类
    sorts = {c.get("sort") for c in calls}
    assert None in sorts or "sort" not in calls[0]  # best match: no sort key
    assert "updated" in sorts
    # 多 query 变体
    qs = {c.get("q") for c in calls}
    assert len(qs) >= 2


@pytest.mark.asyncio
async def test_github_code_search_only_with_token(monkeypatch):
    calls: list[str] = []

    async def fake(url, **kw):
        calls.append(url)
        if "search/code" in url:
            return {
                "items": [
                    {
                        "name": "model.py",
                        "repository": {
                            "html_url": "https://github.com/org/from-code",
                            "full_name": "org/from-code",
                            "description": "found via code",
                            "stargazers_count": 10,
                            "forks_count": 1,
                        },
                    }
                ]
            }
        return {
            "items": [
                {
                    "full_name": "org/repo",
                    "html_url": "https://github.com/org/repo",
                    "description": "base",
                    "stargazers_count": 5,
                    "forks_count": 0,
                    "pushed_at": "2026-01-01T00:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(
        "research_tool.infrastructure.search.github_backend.get_json", fake
    )
    # no token → no code endpoint
    await GitHubBackend(token=None, enable_code_search=True).search("vggt", 5)
    assert not any("search/code" in u for u in calls)

    calls.clear()
    hits = await GitHubBackend(token="t", enable_code_search=True).search(  # noqa: S106
        "vggt", 10
    )
    assert any("search/code" in u for u in calls)
    assert any(h.title == "org/from-code" for h in hits)
    assert any(h.source_engine == "github_code" for h in hits)


@pytest.mark.asyncio
async def test_expert_scoped_github_wired(tmp_path, monkeypatch):
    yaml = """
version: 1
experts:
  - id: fair
    domains: [vggt, reconstruction, 3d-vision]
    handles: {github: facebookresearch}
    seed_urls: []
    priority: high
"""
    p = tmp_path / "experts.yaml"
    p.write_text(yaml, encoding="utf-8")
    cfg = CollectorConfig(
        experts_file=str(p),
        search_engines=["github"],
        search_cache=False,
        expert_scoped_github=True,
        expert_match_min_overlap=0.1,
        search_relevance_min_overlap=0.0,
    )
    c = Collector(cfg)

    async def fake_search(self, query, max_results, language="both", **kw):
        if query.startswith("org:facebookresearch"):
            return [
                SearchHit(
                    url="https://github.com/facebookresearch/vggt-omega",
                    title="facebookresearch/vggt-omega",
                    snippet="★100",
                    source_engine="github",
                )
            ]
        return [
            SearchHit(
                url="https://github.com/random/other",
                title="random/other",
                snippet="★1",
                source_engine="github",
            )
        ]

    monkeypatch.setattr(
        "research_tool.infrastructure.search.github_backend.GitHubBackend.search",
        fake_search,
    )
    sr = await c.search_only("vggt reconstruction geometry")
    urls = [h.url for h in sr.hits]
    assert "https://github.com/facebookresearch/vggt-omega" in urls
    # expert scoped 在搜索结果前部且 expert=True
    exp = next(h for h in sr.hits if "vggt-omega" in h.url)
    assert exp.expert is True


@pytest.mark.asyncio
async def test_fetcher_prefers_raw_readme(monkeypatch):
    f = Fetcher(prefer_crawl4ai=False, timeout_sec=10, parse_pdf=False)
    monkeypatch.setattr(
        "research_tool.infrastructure.stages.fetcher.assert_safe_url",
        lambda _url: None,
    )

    class Resp:
        def __init__(self, status, text="", json_data=None):
            self.status_code = status
            self.text = text
            self._json = json_data or {}

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            if "api.github.com/repos/" in url:
                return Resp(
                    200,
                    json_data={
                        "description": "VGGT Omega",
                        "stargazers_count": 3480,
                        "forks_count": 196,
                        "language": "Python",
                        "homepage": "https://vggt-omega.github.io/",
                        "topics": ["3d", "cvpr"],
                    },
                )
            if "raw.githubusercontent.com" in url and "README.md" in url:
                return Resp(200, text="# VGGT-Omega\n\n## Installation\n\npip install .\n" * 3)
            return Resp(404, text="")

    monkeypatch.setattr(
        "research_tool.infrastructure.stages.fetcher.httpx.AsyncClient", Client
    )

    async def boom(url):
        raise AssertionError("should not fall back to httpx HTML")

    monkeypatch.setattr(f, "_fetch_httpx", boom)
    r = await f.fetch("https://github.com/facebookresearch/vggt-omega")
    assert r.ok
    assert "Installation" in r.markdown
    assert "3480" in r.markdown or "stars" in r.markdown
    assert "raw.githubusercontent.com" in r.markdown
