"""PDF 解析路由、垃圾过滤、二进制兜底的回归测试。"""

import pytest

from src.domain.models import CleanerConfig, CollectorConfig
from src.infrastructure.stages.cleaner import Cleaner, _looks_binary
from src.infrastructure.stages.collector import Collector
from src.infrastructure.stages.fetcher import FetchResult, Fetcher


def test_looks_binary_detects_pdf():
    assert _looks_binary("%PDF-1.5\n/Filter/FlateDecode stream ����")
    # 真实场景：PDF 字节被当 UTF-8 解码后充斥替换符 �
    assert _looks_binary("�" * 200 + "some text")
    assert _looks_binary("\x00\x01\x02\x03" * 100 + "abc")
    assert not _looks_binary("这是一段正常的中文论文摘要内容，讨论上下文学习与知识图谱。")


def test_cleaner_drops_binary_keeps_header():
    raw = (
        "<!-- source: https://x.org/paper.pdf -->\n\n"
        "%PDF-1.4\n/Filter/FlateDecode\nstream\n" + "\x00\x01\x02����" * 200
    )
    out = Cleaner(CleanerConfig()).clean_text(raw)
    assert out.startswith("<!-- source")
    assert "FlateDecode" not in out
    assert "stream" not in out


@pytest.mark.asyncio
async def test_fetcher_pdf_skipped_without_mineru(monkeypatch):
    f = Fetcher(mineru_cmd="definitely-not-a-real-mineru-xyz")
    monkeypatch.setattr(
        "src.infrastructure.ingest.pdf.find_mineru", lambda cmd=None: None
    )
    r = await f._pdf_bytes_to_md("https://x.org/p.pdf", b"%PDF-1.4 ...")
    assert not r.ok and "mineru" in r.error


@pytest.mark.asyncio
async def test_collector_filters_short_docs(monkeypatch, tmp_path):
    cfg = CollectorConfig(min_doc_chars=200, depth=2)
    c = Collector(cfg)

    from src.infrastructure.search.base import SearchHit, SearchResult

    async def fake_search_only(topic):
        return SearchResult(
            hits=[
                SearchHit(url="https://good.com/a", title="good"),
                SearchHit(url="https://junk.com/login", title="login"),
            ]
        )

    async def fake_fetch(url):
        if "junk" in url:
            return FetchResult(url, "Sign in", ok=True)  # 太短
        return FetchResult(url, "实质内容。" * 60, ok=True)

    monkeypatch.setattr(c, "search_only", fake_search_only)
    monkeypatch.setattr(Fetcher, "fetch", lambda self, url: fake_fetch(url))

    res = await c.run("t", tmp_path / "out")
    urls = [s.url for s in res.sources]
    assert "https://good.com/a" in urls
    assert "https://junk.com/login" not in urls  # 垃圾被过滤
