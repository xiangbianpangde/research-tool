"""新增搜索后端解析测试（semantic_scholar / wikipedia / github）。

不打真实网络：monkeypatch 各后端模块里 import 进来的 get_json。
"""

import pytest

from research_tool.search.github_backend import GitHubBackend
from research_tool.search.semantic_scholar import SemanticScholarBackend
from research_tool.search.wikipedia_backend import WikipediaBackend


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

    monkeypatch.setattr("research_tool.search.semantic_scholar.get_json", fake)
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

    monkeypatch.setattr("research_tool.search.wikipedia_backend.get_json", fake)
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

    monkeypatch.setattr("research_tool.search.github_backend.get_json", fake)
    hits = await GitHubBackend().search("transformer", 10)
    assert hits[0].title == "huggingface/transformers"
    assert "★120000" in hits[0].snippet and "Python" in hits[0].snippet


@pytest.mark.asyncio
async def test_pubmed_two_step(monkeypatch):
    from research_tool.search.pubmed_backend import PubMedBackend

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

    monkeypatch.setattr("research_tool.search.pubmed_backend.get_json", fake)
    hits = await PubMedBackend().search("crispr", 10)
    assert [h.url for h in hits] == [
        "https://pubmed.ncbi.nlm.nih.gov/111/",
        "https://pubmed.ncbi.nlm.nih.gov/222/",
    ]
    assert "Nature" in hits[0].snippet and "Doudna J" in hits[0].snippet


@pytest.mark.asyncio
async def test_google_news_parses_rss(monkeypatch):
    from research_tool.search.google_news import GoogleNewsBackend

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

    monkeypatch.setattr("research_tool.search.google_news.get_text", fake)
    hits = await GoogleNewsBackend().search("ai", 10)
    assert hits[0].title == "AI breakthrough"
    assert hits[0].url.endswith("/abc")
    assert "<a" not in hits[0].snippet and "Some outlet reports progress" in hits[0].snippet


@pytest.mark.asyncio
async def test_backend_failure_raises_searcherror(monkeypatch):
    from research_tool.errors import SearchError

    async def boom(url, **kw):
        raise RuntimeError("429 forever")

    monkeypatch.setattr("research_tool.search.github_backend.get_json", boom)
    with pytest.raises(SearchError):
        await GitHubBackend().search("x", 5)
