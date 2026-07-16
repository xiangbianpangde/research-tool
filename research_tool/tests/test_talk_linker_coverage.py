from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_tool.application.talk_linker import (
    PaperCandidate,
    TalkLinker,
    TalkMatch,
    _channel_from_snippet,
    _parse_year,
    title_similarity,
)
from research_tool.domain.models import TalkConfig


def test_talk_helpers_cover_empty_and_invalid_inputs():
    assert title_similarity("", "paper") == 0.0
    assert _parse_year("", "paper from 1989", "future 2101") is None
    assert _channel_from_snippet("") == ""
    assert TalkLinker().pick_best("paper title", []).reason == "no hits"


def test_candidates_from_sources_handles_missing_invalid_and_duplicate_rows(tmp_path, caplog):
    linker = TalkLinker()
    raw = tmp_path / "raw"

    assert linker.candidates_from_sources(raw) == []
    raw.mkdir()
    sources = raw / "sources.json"
    sources.write_text("{invalid", encoding="utf-8")
    assert linker.candidates_from_sources(raw) == []
    assert "sources.json" in caplog.text

    sources.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert linker.candidates_from_sources(raw) == []
    sources.write_text(
        json.dumps(
            [
                "not-a-dict",
                {"title": "short", "url": "https://arxiv.org/abs/1"},
                {
                    "title": "A Complete Paper Title",
                    "url": "https://arxiv.org/abs/1",
                    "source_engine": "arxiv",
                },
                {
                    "title": "A Complete Paper Title",
                    "url": "https://arxiv.org/abs/1",
                    "source_engine": "arxiv",
                },
            ]
        ),
        encoding="utf-8",
    )

    candidates = linker.candidates_from_sources(raw)

    assert [candidate.title for candidate in candidates] == ["A Complete Paper Title"]


def test_candidates_from_clean_headers_filters_bad_rows_and_read_errors(tmp_path, monkeypatch):
    linker = TalkLinker()
    clean = tmp_path / "clean"
    assert linker.candidates_from_clean_headers(clean) == []
    clean.mkdir()
    (clean / "01-valid.md").write_text(
        "<!-- title: Valid Research Paper -->\n"
        "<!-- source: https://arxiv.org/abs/1234 -->\nbody",
        encoding="utf-8",
    )
    (clean / "02-duplicate.md").write_text(
        "<!-- title: Valid Research Paper -->\n"
        "<!-- source: https://arxiv.org/abs/5678 -->",
        encoding="utf-8",
    )
    (clean / "03-blog.md").write_text(
        "<!-- title: Long Blog Article Title -->\n"
        "<!-- source: https://example.com/blog -->",
        encoding="utf-8",
    )
    (clean / "04-no-url.md").write_text(
        "<!-- title: Another Research Paper -->",
        encoding="utf-8",
    )
    (clean / "05-short.md").write_text(
        "<!-- title: short -->\n<!-- source: https://arxiv.org/abs/9 -->",
        encoding="utf-8",
    )
    broken = clean / "00-broken.md"
    broken.write_text("unreadable", encoding="utf-8")
    original_read_text = Path.read_text

    def maybe_fail(self, *args, **kwargs):
        if self == broken:
            raise OSError("read failed")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", maybe_fail)

    candidates = linker.candidates_from_clean_headers(clean)

    assert [candidate.title for candidate in candidates] == ["Valid Research Paper"]


@pytest.mark.parametrize("fallback", ["clean", "raw"])
def test_collect_candidates_uses_both_header_fallbacks(tmp_path, monkeypatch, fallback):
    linker = TalkLinker()
    candidate = PaperCandidate("Fallback Research Paper", "https://arxiv.org/abs/1")
    monkeypatch.setattr(linker, "candidates_from_sources", lambda raw: [])

    def fake_headers(path):
        if path.name == fallback:
            return [candidate]
        return []

    monkeypatch.setattr(linker, "candidates_from_clean_headers", fake_headers)

    assert linker.collect_candidates(tmp_path) == [candidate]


@pytest.mark.asyncio
async def test_find_talk_converts_search_errors_to_unmatched(monkeypatch):
    linker = TalkLinker()

    async def fail_search(*args, **kwargs):
        raise RuntimeError("youtube down")

    monkeypatch.setattr(linker.youtube, "search", fail_search)

    match = await linker.find_talk("A Research Paper", year=2025)

    assert match.matched is False
    assert match.year == 2025
    assert "search error" in match.reason


@pytest.mark.asyncio
async def test_enrich_disabled_and_no_candidates_are_clean_skips(tmp_path):
    disabled = await TalkLinker(TalkConfig(enabled=False)).enrich(tmp_path)
    assert "enabled=false" in disabled.warnings[0]

    linker = TalkLinker(TalkConfig(enabled=True))
    empty = await linker.enrich(tmp_path, force=True)
    assert empty.candidates == 0
    assert (tmp_path / "raw" / ".talk_done").read_text(encoding="utf-8") == "no-candidates\n"


def _matched(paper: PaperCandidate) -> TalkMatch:
    return TalkMatch(
        paper_title=paper.title,
        video_url=f"https://youtube.com/watch?v={paper.title[-1]}",
        confidence=1.0,
        matched=True,
        video_title=f"Talk for {paper.title}",
    )


@pytest.mark.asyncio
async def test_enrich_tracks_skips_and_stops_at_limit(tmp_path, monkeypatch):
    papers = [
        PaperCandidate("Research Paper 1"),
        PaperCandidate("Research Paper 2"),
        PaperCandidate("Research Paper 3"),
    ]
    linker = TalkLinker(TalkConfig(enabled=True, max_talks=1))
    monkeypatch.setattr(linker, "collect_candidates", lambda topic_dir: papers)

    async def fake_find(title, year=None, authors=None):
        paper = next(paper for paper in papers if paper.title == title)
        if title.endswith("1"):
            return TalkMatch(title, None, 0.0, False, reason="below threshold")
        return _matched(paper)

    monkeypatch.setattr(linker, "find_talk", fake_find)

    report = await linker.enrich(tmp_path, force=True)

    assert len(report.skipped) == 1
    assert len(report.matched) == 1
    assert len(report.files_written) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ingest_fails", [False, True])
async def test_enrich_ingest_success_partial_failure_and_exception(
    tmp_path, monkeypatch, ingest_fails
):
    paper = PaperCandidate("Research Paper 1")
    linker = TalkLinker(TalkConfig(enabled=True, max_talks=1, ingest=True))
    monkeypatch.setattr(linker, "collect_candidates", lambda topic_dir: [paper])
    monkeypatch.setattr(linker, "find_talk", lambda *args, **kwargs: None)

    async def fake_find(*args, **kwargs):
        return _matched(paper)

    async def fake_process_videos(**kwargs):
        if ingest_fails:
            raise RuntimeError("transcriber unavailable")
        return SimpleNamespace(failed_count=1, success_count=0)

    monkeypatch.setattr(linker, "find_talk", fake_find)
    monkeypatch.setattr(
        "research_tool.application.video_pipeline.process_videos",
        fake_process_videos,
    )

    report = await linker.enrich(tmp_path, topic="talks", force=True)

    if ingest_fails:
        assert report.ingested_urls == []
        assert any("VideoIngest 失败" in warning for warning in report.warnings)
    else:
        assert len(report.ingested_urls) == 1
        assert any("部分失败" in warning for warning in report.warnings)
