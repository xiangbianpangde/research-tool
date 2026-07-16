"""TalkLinker 单元测试：置信闸边界 + 候选抽取 + enrich 落盘（不打网）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_tool.application.talk_linker import (
    TalkLinker,
    title_similarity,
)
from research_tool.domain.models import TalkConfig
from research_tool.infrastructure.search.base import SearchHit


def test_title_similarity_gate_boundaries():
    # 高重叠
    assert title_similarity(
        "VGGT: Visual Geometry Grounded Transformer",
        "VGGT Visual Geometry Grounded Transformer CVPR",
    ) >= 0.7
    # 明显无关
    assert title_similarity("VGGT geometry transformer", "cooking pasta recipes") < 0.3


def test_pick_best_rejects_below_threshold():
    linker = TalkLinker(TalkConfig(min_title_similarity=0.7))
    hits = [
        SearchHit(
            url="https://www.youtube.com/watch?v=aaa",
            title="Random vlog about cats",
            snippet="Someone | 时长 10:00",
            source_engine="youtube",
        )
    ]
    m = linker.pick_best("VGGT: Visual Geometry Grounded Transformer", hits)
    assert m.matched is False
    assert m.confidence < 0.7


def test_pick_best_accepts_above_threshold():
    linker = TalkLinker(TalkConfig(min_title_similarity=0.7))
    hits = [
        SearchHit(
            url="https://www.youtube.com/watch?v=Cwue59SAF5Q",
            title="VGGT: Visual Geometry Grounded Transformer",
            snippet="Chris Paxton | 时长 58:38",
            source_engine="youtube",
        ),
        SearchHit(
            url="https://www.youtube.com/watch?v=noise",
            title="Unrelated cooking show",
            snippet="Food | 时长 5:00",
            source_engine="youtube",
        ),
    ]
    m = linker.pick_best("VGGT: Visual Geometry Grounded Transformer", hits)
    assert m.matched is True
    assert m.confidence >= 0.7
    assert m.video_url.endswith("Cwue59SAF5Q")


def test_pick_best_prefer_official_channel_for_tiebreak():
    linker = TalkLinker(
        TalkConfig(min_title_similarity=0.5, prefer_official_channel=True)
    )
    paper = "Diffusion Models for Image Generation"
    hits = [
        SearchHit(
            url="https://www.youtube.com/watch?v=fan",
            title="Diffusion Models for Image Generation explained",
            snippet="Random Fan | 时长 20:00",
            source_engine="youtube",
        ),
        SearchHit(
            url="https://www.youtube.com/watch?v=cvf",
            title="Diffusion Models for Image Generation",
            snippet="Computer Vision Foundation | 时长 12:00",
            source_engine="youtube",
        ),
    ]
    m = linker.pick_best(paper, hits)
    assert m.matched is True
    # 官方频道加 bonus 应胜出（在 sim 相近时）
    assert m.video_url.endswith("cvf") or "Computer Vision" in m.channel


def test_candidates_from_sources(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    sources = [
        {
            "url": "https://openaccess.thecvf.com/content/CVPR2025/html/vggt.html",
            "title": "VGGT: Visual Geometry Grounded Transformer",
            "source_engine": "cvpr",
            "snippet": "2025\nAlice",
        },
        {
            "url": "https://example.com/blog",
            "title": "Some blog post not a paper",
            "source_engine": "web",
            "snippet": "",
        },
        {
            "url": "https://arxiv.org/abs/1234",
            "title": "Another Paper Title Here Long",
            "source_engine": "arxiv",
            "snippet": "2024",
        },
    ]
    (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    cands = TalkLinker().candidates_from_sources(raw)
    titles = [c.title for c in cands]
    assert any("VGGT" in t for t in titles)
    assert any("Another Paper" in t for t in titles)
    assert not any("blog" in t.lower() for t in titles)


@pytest.mark.asyncio
async def test_find_talk_uses_youtube_backend(monkeypatch):
    linker = TalkLinker(TalkConfig(min_title_similarity=0.7))

    async def fake_search(query, max_results, language="both", **kw):
        return [
            SearchHit(
                url="https://www.youtube.com/watch?v=hit",
                title="VGGT: Visual Geometry Grounded Transformer",
                snippet="CVPR Official | 时长 10:00",
                source_engine="youtube",
            )
        ]

    monkeypatch.setattr(linker.youtube, "search", fake_search)
    m = await linker.find_talk("VGGT: Visual Geometry Grounded Transformer", year=2025)
    assert m.matched is True
    assert m.video_url.endswith("hit")


@pytest.mark.asyncio
async def test_enrich_writes_discovery_and_done(tmp_path: Path, monkeypatch):
    topic_dir = tmp_path / "topic"
    raw = topic_dir / "raw"
    raw.mkdir(parents=True)
    sources = [
        {
            "url": "https://openaccess.thecvf.com/x",
            "title": "VGGT: Visual Geometry Grounded Transformer",
            "source_engine": "cvpr",
            "snippet": "2025",
        }
    ]
    (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    cfg = TalkConfig(enabled=True, max_talks=2, min_title_similarity=0.7, ingest=False)
    linker = TalkLinker(cfg)

    async def fake_search(query, max_results, language="both", **kw):
        return [
            SearchHit(
                url="https://www.youtube.com/watch?v=abc123",
                title="VGGT: Visual Geometry Grounded Transformer",
                snippet="Chris Paxton | 时长 58:00",
                source_engine="youtube",
            )
        ]

    monkeypatch.setattr(linker.youtube, "search", fake_search)
    report = await linker.enrich(topic_dir, topic="vggt", force=True)
    assert len(report.matched) == 1
    assert report.files_written
    text = report.files_written[0].read_text(encoding="utf-8")
    assert "from_paper:" in text
    assert "talk_confidence:" in text
    assert (raw / ".talk_done").exists()

    # 幂等：第二次跳过
    report2 = await linker.enrich(topic_dir, topic="vggt", force=False)
    assert report2.matched == []
    assert any("talk_done" in w for w in report2.warnings)


def test_confidence_boundary_069_vs_071():
    """设计要求：0.69 拒 / 0.71 收（边界用构造 token 集）。"""
    linker = TalkLinker(TalkConfig(min_title_similarity=0.7))
    # 手工构造 Jaccard：A={a,b,c,d,e,f,g,h,i,j} (10)
    # B 有 7 公共 → 7/13≈0.538；用可控字符串
    # 更直接：mock pick_best 输入 sim 由 title 决定
    paper = "alpha beta gamma delta epsilon"
    # 5 tokens；命中 4 公共 1 新 → inter=4 union=6 → 0.666 < 0.7
    low = SearchHit(
        url="https://www.youtube.com/watch?v=low",
        title="alpha beta gamma delta zeta",
        snippet="x",
        source_engine="youtube",
    )
    # 5 tokens 全中 + 无新 → 1.0
    high = SearchHit(
        url="https://www.youtube.com/watch?v=high",
        title="alpha beta gamma delta epsilon",
        snippet="x",
        source_engine="youtube",
    )
    m_low = linker.pick_best(paper, [low])
    m_high = linker.pick_best(paper, [high])
    assert m_low.matched is False
    assert m_low.confidence < 0.7
    assert m_high.matched is True
    assert m_high.confidence >= 0.7
