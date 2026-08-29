"""Fetcher：arXiv 全文抓取 + 原始资料落盘（raw/_originals）。

覆盖 2026 修复：
- arxiv abs/pdf 链接解析（含无 .pdf 后缀的 pdf 页）
- abs 页有 HTML5 全文 → 返回全文 markdown，并保存原始 HTML
- 老论文无 HTML5 → PDF 字节落盘 _pdfs/_originals + 回退摘要页
- originals index.jsonl 按 URL 去重
"""

from __future__ import annotations

import json

import pytest

from research_tool.infrastructure.stages import fetcher as fetcher_mod
from research_tool.infrastructure.stages.fetcher import Fetcher

_ARXIV_HTML = ("<html><head><title>Paper</title></head><body>"
               "<h1>Agentic Science</h1><p>" + ("full text body " * 400) + "</p></body></html>")


class _Response:
    def __init__(
        self,
        status: int = 200,
        *,
        text: str = "",
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode()
        self.headers = headers or {}
        self._json_data = None

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ScriptedClient:
    script: list[object] = []
    init_kwargs: list[dict] = []

    def __init__(self, **kwargs):
        self.init_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def get(self, _url: str):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _ScriptedClient.script = []
    _ScriptedClient.init_kwargs = []
    monkeypatch.setattr(fetcher_mod.httpx, "AsyncClient", _ScriptedClient)


def test_arxiv_url_parsing():
    cases = {
        "https://arxiv.org/abs/2305.14128": ("abs", "2305.14128"),
        "http://arxiv.org/pdf/2607.02703v1": ("pdf", "2607.02703"),
        "https://arxiv.org/pdf/2607.06820": ("pdf", "2607.06820"),
        "https://arxiv.org/abs/2607.02703v1?x=1": ("abs", "2607.02703"),
        "https://arxiv.org/abs/2305.14128/": ("abs", "2305.14128"),
        "https://example.com/not-arxiv": None,
        "https://arxiv.org/list/cs.CL/new": None,
    }
    for url, expected in cases.items():
        assert fetcher_mod._arxiv_paper_url(url) == expected


@pytest.mark.asyncio
async def test_arxiv_abs_with_html_fulltext_uses_fulltext(tmp_path):
    _ScriptedClient.script = [_Response(200, text=_ARXIV_HTML)]
    f = Fetcher(prefer_crawl4ai=False, originals_dir=tmp_path / "_originals")
    r = await f.fetch("https://arxiv.org/abs/2607.02703")
    assert r.ok
    assert "full text body" in r.markdown
    assert len(r.markdown) > 3000  # 全文而非摘要
    # 原始 HTML 落盘 + index 记录
    originals = tmp_path / "_originals"
    htmls = list(originals.glob("*.html"))
    assert len(htmls) == 1
    index = originals / "index.jsonl"
    assert index.exists()
    first = json.loads(index.read_text().splitlines()[0])
    assert first["url"] == "https://arxiv.org/abs/2607.02703"
    assert first["file"] == htmls[0].name


@pytest.mark.asyncio
async def test_arxiv_old_paper_without_html_saves_pdf_and_falls_back_to_abstract(tmp_path):
    # 第 1、2 个请求：html 版本及其 v1 均 404；第 3 个请求：pdf 下载成功（%PDF 字节）；
    # 第 4 个请求：abs 页（摘要）成功
    _ScriptedClient.script = [
        _Response(404, text="not found"),
        _Response(404, text="not found"),
        _Response(200, content=b"%PDF-1.4 fake pdf bytes"),
        _Response(200, text="<html><body><h1>Abstract page</h1><p>the abstract</p></body></html>"),
    ]
    f = Fetcher(prefer_crawl4ai=False, originals_dir=tmp_path / "_originals", pdf_dir=tmp_path / "_pdfs")
    r = await f.fetch("https://arxiv.org/abs/2305.14128")
    assert r.ok
    assert "abstract" in r.markdown  # 回退摘要页
    assert len(list((tmp_path / "_pdfs").glob("*.pdf"))) == 1  # PDF 字节已落盘
    index = (tmp_path / "_originals" / "index.jsonl").read_text()
    assert '"content_type": "application/pdf"' in index


@pytest.mark.asyncio
async def test_arxiv_pdf_url_without_suffix_routes_to_arxiv(tmp_path):
    """无 .pdf 后缀的 arxiv pdf 页：不再喂给 crawl4ai（会失败），走 arxiv 全文。"""
    _ScriptedClient.script = [_Response(200, text=_ARXIV_HTML)]
    f = Fetcher(prefer_crawl4ai=True, originals_dir=tmp_path / "_originals")
    f.use_crawl4ai = True  # 强制 crawl 可用，验证 dispatch 仍走 arxiv 分支

    async def boom(url):
        raise AssertionError(f"不应进入 crawl4ai: {url}")

    f._fetch_crawl4ai = boom  # type: ignore[method-assign]
    r = await f.fetch("https://arxiv.org/pdf/2607.02703v1")
    assert r.ok
    assert "full text body" in r.markdown


@pytest.mark.asyncio
async def test_originals_index_dedupes_by_url(tmp_path):
    _ScriptedClient.script = [_Response(200, text=_ARXIV_HTML)]
    f = Fetcher(prefer_crawl4ai=False, originals_dir=tmp_path / "_originals")
    await f.fetch("https://arxiv.org/abs/2607.02703")
    # 再次抓同一 URL → html 已存在，index 不重复追加
    _ScriptedClient.script = [_Response(200, text=_ARXIV_HTML)]
    await f.fetch("https://arxiv.org/abs/2607.02703")
    index = (tmp_path / "_originals" / "index.jsonl").read_text().splitlines()
    urls = [json.loads(ln)["url"] for ln in index]
    assert urls.count("https://arxiv.org/abs/2607.02703") == 1


@pytest.mark.asyncio
async def test_save_originals_disabled_writes_nothing(tmp_path):
    _ScriptedClient.script = [_Response(200, text=_ARXIV_HTML)]
    f = Fetcher(
        prefer_crawl4ai=False,
        originals_dir=tmp_path / "_originals",
        save_originals=False,
    )
    r = await f.fetch("https://arxiv.org/abs/2607.02703")
    assert r.ok and len(r.markdown) > 3000
    assert not (tmp_path / "_originals").exists()
