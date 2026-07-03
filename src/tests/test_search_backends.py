"""新增搜索后端解析测试（semantic_scholar / wikipedia / github）。

不打真实网络：monkeypatch 各后端模块里 import 进来的 get_json。
"""

import pytest

from src.infrastructure.search.github_backend import GitHubBackend
from src.infrastructure.search.semantic_scholar import SemanticScholarBackend
from src.infrastructure.search.wikipedia_backend import WikipediaBackend
from src.domain.models import CollectorConfig


@pytest.mark.asyncio
async def test_semantic_scholar_prefers_arxiv_url(monkeypatch):
    async def fake(url, **kw):
        return {
            "data": [
                {
                    "title": "Attention Is All You Need",
                    "abstract": "We propose the Transformer.",
                    "year": 2017,
                    "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
                    "citationCount": 100000,
                    "externalIds": {"ArXiv": "1706.03762"},
                    "url": "https://www.semanticscholar.org/paper/x",
                }
            ]
        }

    monkeypatch.setattr("src.infrastructure.search.semantic_scholar.get_json", fake)
    hits = await SemanticScholarBackend().search("transformer", 10)
    assert len(hits) == 1
    h = hits[0]
    assert h.url == "https://arxiv.org/abs/1706.03762"  # 开放链接优先 arXiv
    assert h.source_engine == "semantic_scholar"
    assert "2017" in h.snippet and "被引 100000" in h.snippet


@pytest.mark.asyncio
async def test_wikipedia_both_langs_and_strips_html(monkeypatch):
    async def fake(url, **kw):
        lang = "en" if "en.wikipedia" in url else "zh"
        return {
            "pages": [
                {
                    "key": f"Yilin_Kang_{lang}",
                    "title": f"Yilin Kang ({lang})",
                    "excerpt": 'a <span class="searchmatch">researcher</span> in AI',
                    "description": "computer scientist",
                }
            ]
        }

    monkeypatch.setattr("src.infrastructure.search.wikipedia_backend.get_json", fake)
    hits = await WikipediaBackend().search("Yilin Kang", 10, language="both")
    # both → 同时搜 en + zh 两个站点
    assert {h.url.split("/wiki/")[0] for h in hits} == {
        "https://en.wikipedia.org",
        "https://zh.wikipedia.org",
    }
    assert all("<span" not in h.snippet for h in hits)  # HTML 高亮被去掉
    assert any("researcher" in h.snippet for h in hits)


@pytest.mark.asyncio
async def test_github_repo_metadata(monkeypatch):
    async def fake(url, **kw):
        return {
            "items": [
                {
                    "full_name": "huggingface/transformers",
                    "html_url": "https://github.com/huggingface/transformers",
                    "description": "SOTA NLP",
                    "stargazers_count": 120000,
                    "forks_count": 24000,
                    "language": "Python",
                }
            ]
        }

    monkeypatch.setattr("src.infrastructure.search.github_backend.get_json", fake)
    hits = await GitHubBackend().search("transformer", 10)
    assert hits[0].title == "huggingface/transformers"
    assert "★120000" in hits[0].snippet and "Python" in hits[0].snippet


@pytest.mark.asyncio
async def test_pubmed_two_step(monkeypatch):
    from src.infrastructure.search.pubmed_backend import PubMedBackend

    async def fake(url, **kw):
        if "esearch" in url:
            return {"esearchresult": {"idlist": ["111", "222"]}}
        return {
            "result": {
                "uids": ["111", "222"],
                "111": {
                    "title": "CRISPR review",
                    "authors": [{"name": "Doudna J"}],
                    "fulljournalname": "Nature",
                    "pubdate": "2020",
                },
                "222": {"title": "Gene therapy", "source": "Cell", "pubdate": "2021"},
            }
        }

    monkeypatch.setattr("src.infrastructure.search.pubmed_backend.get_json", fake)
    hits = await PubMedBackend().search("crispr", 10)
    assert [h.url for h in hits] == [
        "https://pubmed.ncbi.nlm.nih.gov/111/",
        "https://pubmed.ncbi.nlm.nih.gov/222/",
    ]
    assert "Nature" in hits[0].snippet and "Doudna J" in hits[0].snippet


@pytest.mark.asyncio
async def test_google_news_parses_rss(monkeypatch):
    from src.infrastructure.search.google_news import GoogleNewsBackend

    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item>
        <title>AI breakthrough</title>
        <link>https://news.google.com/rss/articles/abc</link>
        <description>&lt;a href="x"&gt;Some outlet&lt;/a&gt; reports progress</description>
        <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    async def fake(url, **kw):
        return rss

    monkeypatch.setattr("src.infrastructure.search.google_news.get_text", fake)
    hits = await GoogleNewsBackend().search("ai", 10)
    assert hits[0].title == "AI breakthrough"
    assert hits[0].url.endswith("/abc")
    assert "<a" not in hits[0].snippet and "Some outlet reports progress" in hits[0].snippet


@pytest.mark.asyncio
async def test_openalex_mixes_relevant_and_recent(monkeypatch):
    from src.infrastructure.search.openalex_backend import OpenAlexBackend

    async def fake(url, **kw):
        sort = (kw.get("params") or {}).get("sort")
        if sort == "publication_date:desc":
            return {"results": [{
                "id": "https://openalex.org/W2", "title": "Recent 2026 paper",
                "publication_year": 2026, "cited_by_count": 1,
                "authorships": [{"author": {"display_name": "Kang"}}],
                "abstract_inverted_index": {"new": [0], "work": [1]},
                "primary_location": {"landing_page_url": "https://x/recent"},
            }]}
        return {"results": [{
            "id": "https://openalex.org/W1", "title": "Classic high-cite",
            "publication_year": 2017, "cited_by_count": 99999,
            "authorships": [{"author": {"display_name": "Vaswani"}}],
            "abstract_inverted_index": {"attention": [0], "matters": [1]},
            "best_oa_location": {"pdf_url": "https://x/classic.pdf"},
        }]}

    monkeypatch.setattr("src.infrastructure.search.openalex_backend.get_json", fake)
    hits = await OpenAlexBackend().search("transformer", 4)
    titles = [h.title for h in hits]
    assert "Classic high-cite" in titles and "Recent 2026 paper" in titles  # 经典+最新都在
    classic = next(h for h in hits if h.title == "Classic high-cite")
    assert "attention matters" in classic.snippet  # 倒排索引重建出 abstract
    assert classic.url == "https://x/classic.pdf"  # 优先开放 PDF


@pytest.mark.asyncio
async def test_crossref_filters_future_years(monkeypatch):
    from src.infrastructure.search.crossref_backend import CrossrefBackend

    async def fake(url, **kw):
        return {"message": {"items": [
            {
                "title": ["Bad date paper"], "DOI": "10.1/x",
                "URL": "https://doi.org/10.1/x",
                "published": {"date-parts": [[2115]]},  # 脏数据未来年份
                "issued": {"date-parts": [[2019]]},      # 真实年份
                "container-title": ["J. Test"],
                "author": [{"given": "A", "family": "B"}],
            }
        ]}}

    monkeypatch.setattr("src.infrastructure.search.crossref_backend.get_json", fake)
    hits = await CrossrefBackend().search("x", 5)
    assert hits[0].title == "Bad date paper"
    assert "2115" not in hits[0].snippet and "2019" in hits[0].snippet  # 脏年份被滤掉


@pytest.mark.asyncio
async def test_backend_failure_raises_searcherror(monkeypatch):
    from src.domain.errors import SearchError

    async def boom(url, **kw):
        raise RuntimeError("429 forever")

    monkeypatch.setattr("src.infrastructure.search.github_backend.get_json", boom)
    with pytest.raises(SearchError):
        await GitHubBackend().search("x", 5)


@pytest.mark.asyncio
async def test_arxiv_atom_parses_and_filters_year(monkeypatch):
    from src.infrastructure.search.arxiv_backend import ArxivBackend

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/2401.00001v1</id>
        <title>Medical MLLM</title>
        <summary>Multimodal diagnosis.</summary>
        <published>2024-01-01T00:00:00Z</published>
      </entry>
      <entry>
        <id>https://arxiv.org/abs/1901.00001v1</id>
        <title>Old paper</title>
        <summary>Old.</summary>
        <published>2019-01-01T00:00:00Z</published>
      </entry>
    </feed>"""

    async def fake(url, **kw):
        return xml

    monkeypatch.setattr("src.infrastructure.search.arxiv_backend.get_text", fake)
    hits = await ArxivBackend().search("medical mllm", 5, from_year=2024)
    assert len(hits) == 1
    assert hits[0].title == "Medical MLLM"
    assert hits[0].source_engine == "arxiv"


@pytest.mark.asyncio
async def test_x_backend_parses_twitter_cli_json(monkeypatch):
    from src.infrastructure.search.x_backend import XBackend

    monkeypatch.setattr("src.infrastructure.search.x_backend.shutil.which", lambda c: c)

    class R:
        returncode = 0
        stderr = ""
        stdout = '[{"id":"123","username":"alice","text":"medical mllm result"}]'

    monkeypatch.setattr("src.infrastructure.search.x_backend.subprocess.run", lambda *a, **k: R())
    hits = await XBackend(CollectorConfig(search_engines=["x"])).search("medical mllm", 5)
    assert hits[0].url == "https://x.com/alice/status/123"
    assert hits[0].source_engine == "x"


def test_x_backend_parses_json_before_opencli_notice():
    from src.infrastructure.search.x_backend import XBackend

    data = XBackend._loads_json_output('[{"id":"123"}]\n\nExtension update available')
    assert data == [{"id": "123"}]
