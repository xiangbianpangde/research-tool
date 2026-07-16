"""Offline branch coverage for the academic search backends and factory."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from research_tool.domain.errors import SearchError
from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search import get_backend
from research_tool.infrastructure.search.arxiv_backend import ArxivBackend
from research_tool.infrastructure.search.crossref_backend import CrossrefBackend, _year
from research_tool.infrastructure.search.openalex_backend import (
    OpenAlexBackend,
    _reconstruct_abstract,
)
from research_tool.infrastructure.search.pubmed_backend import PubMedBackend
from research_tool.infrastructure.search.semantic_scholar import SemanticScholarBackend


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("web", "web"),
        ("arxiv", "arxiv"),
        ("scholar", "arxiv"),
        ("tavily", "tavily"),
        ("semantic_scholar", "semantic_scholar"),
        ("wikipedia", "wikipedia"),
        ("github", "github"),
        ("pubmed", "pubmed"),
        ("google_news", "google_news"),
        ("openalex", "openalex"),
        ("crossref", "crossref"),
        ("cvpr", "cvpr"),
        ("bilibili", "bilibili"),
        ("youtube", "youtube"),
        ("x", "x"),
        ("twitter", "x"),
    ],
)
def test_factory_builds_every_lazy_backend(name: str, expected: str) -> None:
    fake_credential = "test-value"
    config = CollectorConfig(
        search_cache=False,
        tavily_api_key=fake_credential,
        semantic_scholar_api_key=fake_credential,
        github_token=fake_credential,
        github_code_search=False,
        openalex_mailto="researcher@example.test",
    )

    assert get_backend(name, config).name == expected


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown"):
        get_backend("unknown", CollectorConfig(search_cache=False))


@pytest.mark.asyncio
async def test_arxiv_parameters_filters_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>https://arxiv.org/a</id><title>  New\n Paper </title>
        <summary> useful\n abstract </summary><published>2025-01-01</published></entry>
      <entry><id>https://arxiv.org/b</id><title>Too new</title>
        <summary>x</summary><published>2027-01-01</published></entry>
      <entry><id>https://arxiv.org/c</id><title>Unknown year</title>
        <summary>x</summary><published>unknown</published></entry>
    </feed>"""

    async def fake_get_text(url: str, **kwargs: Any) -> str:
        captured.update(kwargs["params"])
        return xml

    monkeypatch.setattr("research_tool.infrastructure.search.arxiv_backend.get_text", fake_get_text)
    hits = await ArxivBackend().search(
        "query", 1, from_year=2024, to_year=2026, sort="date", offset=-4
    )

    assert [hit.url for hit in hits] == ["https://arxiv.org/a"]
    assert hits[0].title == "New Paper"
    assert captured == {
        "search_query": "all:query",
        "start": 0,
        "max_results": 3,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }


@pytest.mark.asyncio
async def test_arxiv_to_year_filter_skips_future_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>https://arxiv.org/future</id><title>Future</title>
        <summary>x</summary><published>2027-01-01</published></entry>
    </feed>"""

    async def fake_get_text(*args: Any, **kwargs: Any) -> str:
        return xml

    monkeypatch.setattr("research_tool.infrastructure.search.arxiv_backend.get_text", fake_get_text)
    assert await ArxivBackend().search("query", 2, to_year=2026) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message"), [(429, "限流 429"), (500, "搜索失败")])
async def test_arxiv_wraps_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str
) -> None:
    response = httpx.Response(status, request=httpx.Request("GET", "https://arxiv.test"))
    error = httpx.HTTPStatusError("bad", request=response.request, response=response)

    async def fail(*args: Any, **kwargs: Any) -> str:
        raise error

    monkeypatch.setattr("research_tool.infrastructure.search.arxiv_backend.get_text", fail)
    with pytest.raises(SearchError, match=message):
        await ArxivBackend().search("q", 1)


@pytest.mark.asyncio
async def test_arxiv_wraps_non_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("offline")

    monkeypatch.setattr("research_tool.infrastructure.search.arxiv_backend.get_text", fail)
    with pytest.raises(SearchError, match="RuntimeError: offline"):
        await ArxivBackend().search("q", 1)


def test_crossref_year_boundaries() -> None:
    assert _year({"published": {"date-parts": [[1500]]}}) == "1500"
    assert _year({"published": {"date-parts": []}}) == ""


@pytest.mark.asyncio
async def test_crossref_all_parameters_and_record_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs["params"])
        return {
            "message": {
                "items": [
                    {"title": ["no address"]},
                    {
                        "title": ["DOI only"],
                        "DOI": "10.1/only",
                        "published-online": {"date-parts": [[2024]]},
                        "container-title": ["Journal"],
                        "author": [{"given": "A", "family": "B"}],
                    },
                    {"title": ["not reached"], "URL": "https://example.test/2"},
                ]
            }
        }

    monkeypatch.setattr("research_tool.infrastructure.search.crossref_backend.get_json", fake_get_json)
    hits = await CrossrefBackend("mail@example.test").search(
        "q", 1, from_year=2020, to_year=2025, sort="citations", offset=4
    )

    assert [hit.url for hit in hits] == ["https://doi.org/10.1/only"]
    assert captured == {
        "query": "q",
        "rows": 1,
        "mailto": "mail@example.test",
        "filter": "from-pub-date:2020-01-01,until-pub-date:2025-12-31",
        "offset": 4,
        "sort": "is-referenced-by-count",
        "order": "desc",
    }


@pytest.mark.asyncio
async def test_crossref_empty_and_error_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr("research_tool.infrastructure.search.crossref_backend.get_json", empty)
    assert await CrossrefBackend().search("q", 1) == []

    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("offline")

    monkeypatch.setattr("research_tool.infrastructure.search.crossref_backend.get_json", fail)
    with pytest.raises(SearchError, match="RuntimeError: offline"):
        await CrossrefBackend().search("q", 1)


def test_openalex_helpers_cover_all_url_fallbacks() -> None:
    backend = OpenAlexBackend()
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({"second": [1], "first": [0]}) == "first second"
    assert backend._year_filter(None, None) is None
    assert backend._year_filter(2020, 2025) == (
        "from_publication_date:2020-01-01,to_publication_date:2025-12-31"
    )
    assert backend._best_url({"primary_location": {"pdf_url": "https://x.test/a.pdf"}}).endswith(
        "a.pdf"
    )
    assert backend._best_url({"primary_location": {"landing_page_url": "https://x.test/a"}}) == (
        "https://x.test/a"
    )
    assert backend._best_url({"doi": "https://doi.org/1"}) == "https://doi.org/1"
    assert backend._best_url({"id": "https://openalex.org/W1"}).endswith("W1")
    assert backend._to_hit({}) is None


@pytest.mark.asyncio
async def test_openalex_query_parameters_and_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_get_json(url: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        calls.append(kwargs["params"])
        return [] if len(calls) == 2 else {"results": []}

    monkeypatch.setattr("research_tool.infrastructure.search.openalex_backend.get_json", fake_get_json)
    backend = OpenAlexBackend("mail@example.test")
    assert await backend._query("q", 0, "cited_by_count:desc", flt="x:y", offset=51) == []
    assert await backend._query("q", 100, None) == []
    assert calls == [
        {
            "search": "q",
            "per-page": 1,
            "sort": "cited_by_count:desc",
            "filter": "x:y",
            "page": 52,
            "mailto": "mail@example.test",
        },
        {"search": "q", "per-page": 50, "mailto": "mail@example.test"},
    ]


@pytest.mark.asyncio
async def test_openalex_sorted_search_deduplicates_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    works = [
        {},
        {"id": "https://openalex.org/W1", "title": "one", "cited_by_count": None},
        {"id": "https://openalex.org/W1", "title": "duplicate"},
        {
            "id": "https://openalex.org/W2",
            "title": "two",
            "publication_year": 2025,
            "cited_by_count": 0,
            "authorships": [{"author": {"display_name": "A"}}],
        },
    ]

    async def fake_query(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return works

    backend = OpenAlexBackend()
    monkeypatch.setattr(backend, "_query", fake_query)
    hits = await backend.search("q", 2, from_year=2020, sort="citations", offset=2)
    assert [hit.title for hit in hits] == ["one", "two"]


@pytest.mark.asyncio
async def test_openalex_wraps_query_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("offline")

    backend = OpenAlexBackend()
    monkeypatch.setattr(backend, "_query", fail)
    with pytest.raises(SearchError, match="RuntimeError: offline"):
        await backend.search("q", 2)


@pytest.mark.asyncio
async def test_pubmed_search_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PubMedBackend()

    async def no_ids(*args: Any, **kwargs: Any) -> list[str]:
        return []

    monkeypatch.setattr(backend, "_esearch", no_ids)
    assert await backend.search("q", 2) == []

    async def known_error(*args: Any, **kwargs: Any) -> list[str]:
        raise SearchError("known")

    monkeypatch.setattr(backend, "_esearch", known_error)
    with pytest.raises(SearchError, match="known"):
        await backend.search("q", 2)

    async def unknown_error(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("offline")

    monkeypatch.setattr(backend, "_esearch", unknown_error)
    with pytest.raises(SearchError, match="RuntimeError: offline"):
        await backend.search("q", 2)


@pytest.mark.asyncio
async def test_pubmed_esearch_parameters_and_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_get_json(url: str, **kwargs: Any) -> list[Any]:
        captured.update(kwargs["params"])
        return []

    monkeypatch.setattr("research_tool.infrastructure.search.pubmed_backend.get_json", fake_get_json)
    ids = await PubMedBackend()._esearch(
        "q", 20, from_year=None, to_year=2025, sort="date", offset=3
    )
    assert ids == []
    assert captured["mindate"] == "1500"
    assert captured["maxdate"] == "2025"
    assert captured["sort"] == "pub_date"
    assert captured["retstart"] == 3


@pytest.mark.asyncio
async def test_pubmed_esummary_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr("research_tool.infrastructure.search.pubmed_backend.get_json", fake_get_json)
    assert await PubMedBackend()._esummary(["1"]) == []


def test_semantic_scholar_url_fallbacks() -> None:
    backend = SemanticScholarBackend()
    assert backend._best_url({"openAccessPdf": {"url": "https://x.test/a.pdf"}}).endswith(
        "a.pdf"
    )
    assert backend._best_url({"externalIds": {"DOI": "10.1/x"}}) == "https://doi.org/10.1/x"
    assert backend._best_url({"url": "https://s2.test/paper"}) == "https://s2.test/paper"
    assert backend._best_url({}) == ""


@pytest.mark.asyncio
async def test_semantic_scholar_parameters_sorting_and_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    papers = [
        {"title": "no url", "year": 2030, "citationCount": 999},
        {
            "title": "older",
            "url": "https://s2.test/old",
            "year": 2020,
            "citationCount": 100,
            "authors": [{"name": "A"}],
            "abstract": "abstract",
        },
        {
            "title": "newer",
            "url": "https://s2.test/new",
            "year": 2025,
            "citationCount": 1,
        },
    ]

    async def fake_get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"data": papers}

    monkeypatch.setattr("research_tool.infrastructure.search.semantic_scholar.get_json", fake_get_json)
    backend = SemanticScholarBackend("key")
    by_date = await backend.search("q", 101, from_year=2020, to_year=2025, sort="date", offset=4)
    by_cites = await backend.search("q", 2, sort="citations")

    assert [hit.title for hit in by_date] == ["newer", "older"]
    assert [hit.title for hit in by_cites] == ["older", "newer"]
    assert calls[0]["params"]["limit"] == 100
    assert calls[0]["params"]["year"] == "2020-2025"
    assert calls[0]["params"]["offset"] == 4
    assert calls[0]["headers"] == {"x-api-key": "key"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("with_key", "status", "message"), [(False, 429, "配置"), (True, 429, "搜索失败"), (False, 500, "搜索失败")])
async def test_semantic_scholar_wraps_http_errors(
    monkeypatch: pytest.MonkeyPatch, with_key: bool, status: int, message: str
) -> None:
    response = httpx.Response(status, request=httpx.Request("GET", "https://s2.test"))
    error = httpx.HTTPStatusError("bad", request=response.request, response=response)

    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr("research_tool.infrastructure.search.semantic_scholar.get_json", fail)
    with pytest.raises(SearchError, match=message):
        await SemanticScholarBackend("key" if with_key else None).search("q", 1)


@pytest.mark.asyncio
async def test_semantic_scholar_wraps_non_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("offline")

    monkeypatch.setattr("research_tool.infrastructure.search.semantic_scholar.get_json", fail)
    with pytest.raises(SearchError, match="RuntimeError: offline"):
        await SemanticScholarBackend().search("q", 1)
