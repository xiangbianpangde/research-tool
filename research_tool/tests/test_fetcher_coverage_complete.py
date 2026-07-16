"""Offline behavioral coverage for Fetcher error and fallback paths."""

from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest

from research_tool.domain.errors import UrlBlockedError
from research_tool.infrastructure.stages import fetcher as fetcher_mod
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher


def test_github_metadata_rejects_non_mapping_payload():
    assert fetcher_mod._github_meta_markdown("owner", "repo", "https://example.com", []) == ""


class _Response:
    def __init__(
        self,
        status: int = 200,
        *,
        text: str = "",
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        json_data: object = None,
    ):
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode()
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ScriptedClient:
    script: list[_Response | Exception] = []
    init_kwargs: list[dict] = []
    exits = 0

    def __init__(self, **kwargs):
        self.init_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        type(self).exits += 1

    async def get(self, _url: str):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    _ScriptedClient.script = []
    _ScriptedClient.init_kwargs = []
    _ScriptedClient.exits = 0
    monkeypatch.setattr(fetcher_mod.httpx, "AsyncClient", _ScriptedClient)


def test_crawl4ai_availability_both_import_outcomes(monkeypatch):
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace())
    assert fetcher_mod._crawl4ai_available() is True

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "crawl4ai":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "crawl4ai", raising=False)
    monkeypatch.setattr(builtins, "__import__", missing)
    assert fetcher_mod._crawl4ai_available() is False


@pytest.mark.asyncio
async def test_dispatch_routes_pdf_and_crawl_fallbacks(monkeypatch):
    fetcher = Fetcher(prefer_crawl4ai=False)

    async def pdf(url):
        return FetchResult(url, "pdf")

    async def crawl(url):
        return FetchResult(url, "crawl")

    monkeypatch.setattr(fetcher, "_fetch_pdf", pdf)
    assert (await fetcher._fetch_dispatch("https://example.test/PAPER.PDF?download=1")).markdown == "pdf"

    fetcher.use_crawl4ai = True
    monkeypatch.setattr(fetcher, "_fetch_crawl4ai", crawl)
    assert (await fetcher._fetch_dispatch("https://example.test/page")).markdown == "crawl"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["https://example.test/not-github", "https://github.com/settings/profile", "https://github.com/a/issues"],
)
async def test_github_readme_rejects_non_repository_urls(url):
    assert await Fetcher(prefer_crawl4ai=False)._fetch_github_readme(url) is None


@pytest.mark.asyncio
async def test_github_api_failure_still_uses_lowercase_readme():
    _ScriptedClient.script = [
        RuntimeError("api down"),
        _Response(404),
        _Response(200, text="# lower-case readme\n\n" + "body " * 10),
    ]
    result = await Fetcher(prefer_crawl4ai=False)._fetch_github_readme(
        "https://github.com/owner/repo.git"
    )
    assert result is not None and result.ok
    assert "lower-case readme" in result.markdown
    assert "branch=main" in result.markdown
    assert _ScriptedClient.exits == 2


@pytest.mark.asyncio
async def test_github_raw_failure_returns_none_after_cleanup():
    _ScriptedClient.script = [_Response(404), RuntimeError("raw down")]
    result = await Fetcher(prefer_crawl4ai=False)._fetch_github_readme(
        "https://github.com/owner/repo"
    )
    assert result is None
    assert _ScriptedClient.exits == 2


@pytest.mark.asyncio
async def test_github_meta_only_when_all_readme_variants_missing():
    _ScriptedClient.script = [
        _Response(200, json_data={"description": "metadata only", "stargazers_count": 1}),
        *[_Response(404) for _ in range(6)],
    ]
    result = await Fetcher(prefer_crawl4ai=False)._fetch_github_readme(
        "https://github.com/owner/repo"
    )
    assert result is not None and result.ok
    assert "meta only" in result.markdown


@pytest.mark.asyncio
async def test_github_no_metadata_or_readme_returns_none():
    _ScriptedClient.script = [_Response(404), *[_Response(404) for _ in range(6)]]
    assert (
        await Fetcher(prefer_crawl4ai=False)._fetch_github_readme(
            "https://github.com/owner/repo"
        )
        is None
    )


def test_httpx_kwargs_proxy_and_grace_clamping():
    fetcher = Fetcher(
        prefer_crawl4ai=False,
        timeout_sec=7,
        proxy="http://proxy.test:8080",
        hard_timeout_grace_sec=-2,
    )
    kwargs = fetcher._httpx_client_kwargs()
    assert kwargs["proxy"] == "http://proxy.test:8080"
    assert kwargs["trust_env"] is True
    assert fetcher.hard_timeout_sec() == 7.0


@pytest.mark.asyncio
async def test_fetch_pdf_disabled_download_errors_and_non_pdf_fallback(monkeypatch):
    disabled = await Fetcher(prefer_crawl4ai=False, parse_pdf=False)._fetch_pdf(
        "https://example.test/paper.pdf"
    )
    assert not disabled.ok and "parse_pdf=False" in disabled.error

    _ScriptedClient.script = [UrlBlockedError("private redirect")]
    blocked = await Fetcher(prefer_crawl4ai=False)._fetch_pdf("https://example.test/paper.pdf")
    assert not blocked.ok and "SSRF" in blocked.error

    _ScriptedClient.script = [RuntimeError("download broke")]
    failed = await Fetcher(prefer_crawl4ai=False)._fetch_pdf("https://example.test/paper.pdf")
    assert not failed.ok and "download broke" in failed.error

    _ScriptedClient.script = [_Response(200, content=b"<html>not pdf</html>")]
    fetcher = Fetcher(prefer_crawl4ai=False)

    async def html(url):
        return FetchResult(url, "html fallback")

    monkeypatch.setattr(fetcher, "_fetch_httpx", html)
    assert (await fetcher._fetch_pdf("https://example.test/paper.pdf")).markdown == "html fallback"


@pytest.mark.asyncio
async def test_fetch_pdf_content_is_sent_to_pdf_converter(monkeypatch):
    _ScriptedClient.script = [_Response(200, content=b"%PDF-content")]
    fetcher = Fetcher(prefer_crawl4ai=False)

    async def convert(url, data):
        assert data == b"%PDF-content"
        return FetchResult(url, "converted")

    monkeypatch.setattr(fetcher, "_pdf_bytes_to_md", convert)
    assert (await fetcher._fetch_pdf("https://example.test/paper.pdf")).markdown == "converted"


@pytest.mark.asyncio
async def test_pdf_bytes_disabled_success_and_parse_failure(monkeypatch, tmp_path):
    disabled = await Fetcher(prefer_crawl4ai=False, parse_pdf=False)._pdf_bytes_to_md(
        "https://example.test/a.pdf", b"%PDF"
    )
    assert not disabled.ok

    from research_tool.infrastructure.ingest import pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "find_mineru", lambda _cmd: "/fake/mineru")
    monkeypatch.setattr(pdf_mod, "mineru_to_markdown", lambda *a, **k: "# parsed")
    fetcher = Fetcher(prefer_crawl4ai=False, pdf_dir=tmp_path, mineru_cmd="mineru")
    result = await fetcher._pdf_bytes_to_md("https://example.test/a.pdf", b"%PDF")
    assert result.ok and result.markdown == "# parsed"
    assert len(list(tmp_path.glob("*.pdf"))) == 1

    def fail(*_args, **_kwargs):
        raise RuntimeError("parse broke")

    monkeypatch.setattr(pdf_mod, "mineru_to_markdown", fail)
    failed = await fetcher._pdf_bytes_to_md("https://example.test/b.pdf", b"%PDF")
    assert not failed.ok and "parse broke" in failed.error


class _BrowserConfig:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)


class _CrawlerRunConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Crawler:
    result = None
    exits = 0

    def __init__(self, *, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        type(self).exits += 1

    async def arun(self, url, *, config):
        assert config.kwargs["page_timeout"] == 4000
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fake_crawl4ai(monkeypatch):
    _BrowserConfig.calls = []
    _Crawler.exits = 0
    module = SimpleNamespace(
        AsyncWebCrawler=_Crawler,
        BrowserConfig=_BrowserConfig,
        CrawlerRunConfig=_CrawlerRunConfig,
    )
    monkeypatch.setitem(sys.modules, "crawl4ai", module)
    return module


@pytest.mark.asyncio
async def test_crawl4ai_success_extracts_markdown_links_proxy_and_closes(fake_crawl4ai):
    _Crawler.result = SimpleNamespace(
        success=True,
        markdown=SimpleNamespace(raw_markdown="# rendered"),
        links={"internal": [{"href": "https://example.test/a"}, "https://example.test/b", None]},
    )
    result = await Fetcher(
        prefer_crawl4ai=False, timeout_sec=4, proxy="http://proxy.test:8080"
    )._fetch_crawl4ai("https://example.test")
    assert result.ok and result.markdown == "# rendered"
    assert result.links == ["https://example.test/a", "https://example.test/b"]
    assert _BrowserConfig.calls == [
        {"headless": True, "proxy_config": {"server": "http://proxy.test:8080"}}
    ]
    assert _Crawler.exits == 1


@pytest.mark.asyncio
async def test_crawl4ai_failure_plain_markdown_and_exception_paths(fake_crawl4ai):
    fetcher = Fetcher(prefer_crawl4ai=False, timeout_sec=4, proxy="  ")
    _Crawler.result = SimpleNamespace(success=False, error_message="", markdown="", links=[])
    failed = await fetcher._fetch_crawl4ai("https://example.test")
    assert not failed.ok and failed.error == "crawl 失败"
    assert _BrowserConfig.calls[-1] == {"headless": True}

    _Crawler.result = SimpleNamespace(success=True, markdown="plain", links=[])
    plain = await fetcher._fetch_crawl4ai("https://example.test")
    assert plain.markdown == "plain" and plain.links == []

    _Crawler.result = RuntimeError("crawler broke")
    broken = await fetcher._fetch_crawl4ai("https://example.test")
    assert not broken.ok and "crawler broke" in broken.error


@pytest.mark.asyncio
async def test_httpx_pdf_html_and_generic_error_paths(monkeypatch):
    fetcher = Fetcher(prefer_crawl4ai=False)
    _ScriptedClient.script = [
        _Response(200, content=b"%PDF-data", headers={"content-type": "application/octet-stream"})
    ]

    async def convert(url, data):
        return FetchResult(url, data.decode())

    monkeypatch.setattr(fetcher, "_pdf_bytes_to_md", convert)
    assert (await fetcher._fetch_httpx("https://example.test/doc")).markdown == "%PDF-data"

    html = '<script>x</script><p>A &amp; B</p><a href="https://example.test/x">x</a>'
    _ScriptedClient.script = [_Response(200, text=html, headers={"content-type": "text/html"})]
    rendered = await fetcher._fetch_httpx("https://example.test/page")
    assert rendered.ok and rendered.markdown.startswith("A & B")
    assert rendered.links == ["https://example.test/x"]

    _ScriptedClient.script = [RuntimeError("network broke")]
    failed = await fetcher._fetch_httpx("https://example.test/fail")
    assert not failed.ok and failed.error == "network broke"
    assert _ScriptedClient.exits == 3
