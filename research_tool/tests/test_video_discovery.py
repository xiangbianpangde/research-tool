"""Collector 视频 URL discovery：不走 HTML Fetcher，写 discovery 笔记。"""


import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.collector import Collector, _is_video_url


def test_is_video_url_hosts():
    assert _is_video_url("https://www.youtube.com/watch?v=abc")
    assert _is_video_url("https://youtu.be/abc")
    assert _is_video_url("https://www.bilibili.com/video/BV1xx")
    assert not _is_video_url("https://arxiv.org/abs/1234")
    assert not _is_video_url("https://github.com/foo/bar")


@pytest.mark.asyncio
async def test_fetch_and_store_video_writes_discovery(tmp_path, monkeypatch):
    cfg = CollectorConfig(search_engines=["web"], search_cache=False, depth=2, min_doc_chars=50)
    c = Collector(cfg)
    raw = tmp_path / "raw"
    hits = [
        SearchHit(
            url="https://www.youtube.com/watch?v=Cwue59SAF5Q",
            title="VGGT Talk",
            snippet="Chris Paxton | 时长 58:38",
            source_engine="youtube",
        )
    ]

    # 若误走 Fetcher 会失败：强制让 fetch 抛错以证明视频路径不调用它
    async def boom(url):
        raise AssertionError(f"Fetcher 不应被视频 URL 调用: {url}")


    class FakeFetcher:
        def __init__(self, **kw):
            pass

        async def fetch(self, url):
            return await boom(url)

    monkeypatch.setattr(
        "research_tool.infrastructure.stages.collector.Fetcher", FakeFetcher
    )

    result = await c.fetch_and_store("VGGT", hits, raw)
    assert len(result.files) == 1
    text = result.files[0].read_text(encoding="utf-8")
    assert "video discovery" in text
    assert "youtube.com/watch" in text
    assert "VGGT Talk" in text
