"""ExpertLib 注册表测试：加载 / 匹配 / 注入 / 宽容回退。不打网络。"""

import textwrap
from pathlib import Path

from research_tool.domain.models import ExpertEntry, ExpertLibrary
from research_tool.infrastructure.experts.registry import ExpertRegistry

_YAML = textwrap.dedent(
    """
    version: 1
    experts:
      - id: fair
        name: FAIR
        kind: org
        domains: [3d-vision, reconstruction, sfm]
        handles: {github: facebookresearch}
        seed_urls: [https://github.com/facebookresearch/vggt-omega]
        priority: high
      - id: gr
        name: Google Research
        kind: org
        domains: [nlp, diffusion]
        handles: {github: google-research}
        seed_urls: []
        priority: normal
    """
)


def _reg(tmp_path: Path) -> ExpertRegistry:
    p = tmp_path / "experts.yaml"
    p.write_text(_YAML, encoding="utf-8")
    return ExpertRegistry.load(p)


def test_load_ok(tmp_path):
    reg = _reg(tmp_path)
    assert len(reg.library.experts) == 2


def test_match_by_domain_overlap(tmp_path):
    reg = _reg(tmp_path)
    matched_ids = [e.id for e in reg.match("3D reconstruction", min_overlap=0.15)]
    assert "fair" in matched_ids  # 3d + reconstruction 命中
    assert "gr" not in matched_ids  # 无交集


def test_match_empty_topic(tmp_path):
    reg = _reg(tmp_path)
    assert reg.match("", 0.15) == []


def test_priority_ordering():
    lib = ExpertLibrary(
        experts=[
            ExpertEntry(id="low", domains=["vision"], priority="normal"),
            ExpertEntry(id="high", domains=["vision"], priority="high"),
        ]
    )
    matched = ExpertRegistry(lib).match("vision transformer", min_overlap=0.1)
    assert [e.id for e in matched][0] == "high"  # high 优先排前


def test_seed_urls_dedup(tmp_path):
    reg = _reg(tmp_path)
    matched = reg.match("sfm reconstruction", 0.15)
    assert reg.seed_urls_for(matched) == [
        "https://github.com/facebookresearch/vggt-omega"
    ]


def test_scoped_queries_for_github(tmp_path):
    reg = _reg(tmp_path)
    matched = reg.match("3D reconstruction", 0.15)
    qs = reg.scoped_queries_for(matched, "3D reconstruction")
    assert ("github", "org:facebookresearch 3D reconstruction") in qs


def test_missing_file_returns_empty(tmp_path):
    reg = ExpertRegistry.load(tmp_path / "nope.yaml")
    assert reg.library.experts == []


def test_bad_schema_returns_empty(tmp_path):
    # 能被 yaml 解析但 schema 不合法（experts 应为 list）→ 空库回退，不抛
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\nexperts: not-a-list\n", encoding="utf-8")
    reg = ExpertRegistry.load(p)
    assert reg.library.experts == []


def test_shipped_experts_yaml_valid():
    # 仓库根随附的 experts.yaml 应当能加载通过 schema
    root = Path(__file__).resolve().parents[2]
    shipped = root / "experts.yaml"
    if not shipped.exists():
        return
    reg = ExpertRegistry.load(shipped)
    ids = [e.id for e in reg.library.experts]
    assert "facebookresearch" in ids


# --- Collector 强档直注集成（mock search_queries，不打网络）---


import pytest  # noqa: E402

from research_tool.domain.models import CollectorConfig  # noqa: E402
from research_tool.infrastructure.search.base import SearchHit, SearchResult  # noqa: E402
from research_tool.infrastructure.stages.collector import Collector  # noqa: E402


@pytest.mark.asyncio
async def test_collector_injects_expert_seed(tmp_path, monkeypatch):
    p = tmp_path / "experts.yaml"
    p.write_text(_YAML, encoding="utf-8")
    cfg = CollectorConfig(
        experts_file=str(p), search_engines=["web"], search_cache=False
    )
    c = Collector(cfg)

    async def fake_sq(queries, **kw):
        return SearchResult(
            hits=[SearchHit(url="https://example.com/a", title="A", source_engine="web")],
            warnings=[],
        )

    monkeypatch.setattr(c, "search_queries", fake_sq)
    sr = await c.search_only("3D reconstruction")

    # 专家 seed 置顶、标 expert，且搜索命中仍保留
    assert sr.hits[0].url == "https://github.com/facebookresearch/vggt-omega"
    assert sr.hits[0].expert is True
    assert "https://example.com/a" in [h.url for h in sr.hits]


@pytest.mark.asyncio
async def test_collector_extra_urls_injected(monkeypatch):
    cfg = CollectorConfig(
        extra_urls=["https://github.com/foo/bar"],
        search_engines=["web"],
        search_cache=False,
    )
    c = Collector(cfg)

    async def fake_sq(queries, **kw):
        return SearchResult(hits=[], warnings=[])

    monkeypatch.setattr(c, "search_queries", fake_sq)
    sr = await c.search_only("anything")
    assert sr.hits[0].url == "https://github.com/foo/bar"
    assert sr.hits[0].expert is True


@pytest.mark.asyncio
async def test_collector_no_injection_returns_unchanged(monkeypatch):
    cfg = CollectorConfig(search_engines=["web"], search_cache=False)
    c = Collector(cfg)
    sentinel = SearchResult(
        hits=[SearchHit(url="https://x/y", source_engine="web")], warnings=["w"]
    )

    async def fake_sq(queries, **kw):
        return sentinel

    monkeypatch.setattr(c, "search_queries", fake_sq)
    sr = await c.search_only("anything")
    assert sr is sentinel  # 无注入时原样返回，向后兼容
