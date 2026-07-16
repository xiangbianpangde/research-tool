"""Behavioral coverage for the collect, clean, and extract stages.

These tests deliberately exercise failure and boundary paths without network or
real LLM calls.  They are kept separate from feature-level tests so the coverage
contract is easy to audit.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from research_tool.domain.errors import CollectError, UrlBlockedError
from research_tool.domain.models import (
    CleanerConfig,
    CleanResult,
    CollectResult,
    CollectorConfig,
    ExtractorConfig,
    FileQuality,
)
from research_tool.infrastructure.experts.registry import ExpertRegistry
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.search.base import SearchHit, SearchResult
from research_tool.infrastructure.search.github_backend import GitHubBackend
from research_tool.infrastructure.stages import cleaner as cleaner_module
from research_tool.infrastructure.stages import collector as collector_module
from research_tool.infrastructure.stages import extractor as extractor_module
from research_tool.infrastructure.stages.cleaner import (
    Cleaner,
    _find_content_start,
    _jaccard,
    _looks_binary,
    _read_title_excerpt,
    _shingles,
)
from research_tool.infrastructure.stages.collector import (
    Collector,
    _facet_queries,
    _hit_relevance,
)
from research_tool.infrastructure.stages.extractor import (
    Extractor,
    _ChunkResult,
    _LEntity,
    _LRelation,
    _LTriple,
    _build_prompt,
    _chunk,
)
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher


# ---------------------------------------------------------------------------
# Collector


def test_collector_query_and_relevance_boundaries():
    assert _facet_queries("   ", ["paper"]) == []
    assert _facet_queries("core", ["", " method ", "method"]) == ["core", "core method"]
    assert _hit_relevance(",,,", SearchHit(url="", title="", snippet="")) == 1.0
    assert _hit_relevance("specific concept", SearchHit(url="", title="", snippet="")) == 0.0


@pytest.mark.asyncio
async def test_search_only_combines_llm_extra_facets_and_expert_warning(monkeypatch):
    captured: list[str] = []

    def structured(prompt, schema):
        return schema(queries=[" generated ", ""])

    collector = Collector(
        CollectorConfig(
            search_engines=[],
            llm_query_expansion=True,
            extra_queries=["pinned"],
            core_keyword="core",
            facets=["facet"],
        ),
        MockLLMClient(structured_response=structured),
    )

    async def expert_hits(topic):
        return [], ["expert warning"]

    async def search_queries(queries, **kwargs):
        captured.extend(queries)
        return SearchResult(warnings=["search warning"])

    monkeypatch.setattr(collector, "_expert_scoped_github_hits", expert_hits)
    monkeypatch.setattr(collector, "search_queries", search_queries)

    result = await collector.search_only("topic")

    assert captured == ["pinned", "topic", "generated", "core", "core facet"]
    assert result.warnings == ["search warning", "expert warning"]


@pytest.mark.asyncio
async def test_expert_scoped_github_all_guards_and_results(tmp_path, monkeypatch):
    disabled = Collector(
        CollectorConfig(
            experts_file=str(tmp_path / "unused.yaml"),
            search_engines=["github"],
            expert_scoped_github=False,
        )
    )
    assert await disabled._expert_scoped_github_hits("topic") == ([], [])

    class Registry:
        def match(self, topic, overlap):
            return []

        def scoped_queries_for(self, matched, topic):
            return self.queries

    registry = Registry()
    registry.queries = []
    monkeypatch.setattr(ExpertRegistry, "load", classmethod(lambda cls, path: registry))
    collector = Collector(
        CollectorConfig(experts_file=str(tmp_path / "experts.yaml"), search_engines=["github"])
    )
    assert await collector._expert_scoped_github_hits("topic") == ([], [])

    registry.queries = [
        ("other", "skip"),
        ("github", "boom"),
        ("github", "ok"),
    ]

    async def github_search(self, query, limit):
        if query == "boom":
            raise RuntimeError("rate limited")
        return [
            SearchHit(url="", source_engine="github"),
            SearchHit(url="https://github.com/acme/repo", title="repo", source_engine=""),
            SearchHit(url="https://github.com/acme/repo", title="duplicate"),
        ]

    monkeypatch.setattr(GitHubBackend, "search", github_search)
    hits, warnings = await collector._expert_scoped_github_hits("topic")

    assert [hit.url for hit in hits] == ["https://github.com/acme/repo"]
    assert hits[0].expert is True
    assert hits[0].source_engine == "github"
    assert "rate limited" in warnings[0]


@pytest.mark.asyncio
async def test_deep_search_and_explicit_x_search_cover_combinations(monkeypatch):
    deep = Collector(
        CollectorConfig(
            search_engines=[],
            deep_search=True,
            deep_pages=2,
            deep_sorts=[],
            max_results_per_engine=3,
        )
    )
    assert deep._deep_combos() == [("relevance", 0), ("relevance", 3)]
    assert (await deep.search_queries(["q"])).hits == []

    calls = []

    class Backend:
        async def search(self, query, limit, language, **kwargs):
            calls.append(kwargs)
            return [
                SearchHit(url="https://a.example", title="a"),
                SearchHit(url="https://a.example", title="duplicate"),
                SearchHit(url="https://b.example", title="b"),
            ]

    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.preflight_x",
        lambda config, run_doctor: SimpleNamespace(ok=True, message="x ready"),
    )
    monkeypatch.setattr(collector_module, "get_backend", lambda engine, config: Backend())
    collector = Collector(
        CollectorConfig(
            search_engines=["x"],
            search_relevance_min_overlap=0,
            max_total_results=2,
        )
    )

    result = await collector.search_queries(["q"], sort="date", offset=4)

    assert [hit.url for hit in result.hits] == ["https://a.example", "https://b.example"]
    assert calls == [{"from_year": None, "to_year": None, "sort": "date", "offset": 4}]


@pytest.mark.asyncio
async def test_official_url_block_and_partial_success_abort(tmp_path, monkeypatch):
    blocked = Collector(CollectorConfig(official_urls=["https://official.example/paper"]))
    monkeypatch.setattr(
        collector_module,
        "assert_safe_url",
        lambda url: (_ for _ in ()).throw(UrlBlockedError("private")),
    )
    with pytest.raises(CollectError, match="安全校验"):
        await blocked.run("topic", tmp_path / "blocked")

    monkeypatch.setattr(collector_module, "assert_safe_url", lambda url: None)
    partial = Collector(
        CollectorConfig(
            official_urls=["https://official.example/one", "https://official.example/two"]
        )
    )

    async def one_success(topic, hits, raw_dir):
        path = raw_dir / "one.md"
        path.write_text("body", encoding="utf-8")
        return CollectResult(files=[path], raw_dir=raw_dir)

    monkeypatch.setattr(partial, "fetch_and_store", one_success)
    monkeypatch.setattr(partial, "_load_sources", lambda raw_dir: [])
    with pytest.raises(CollectError, match="未全部"):
        await partial.run("topic", tmp_path / "partial")


@pytest.mark.asyncio
async def test_nonofficial_dry_run_returns_search_warnings(tmp_path, monkeypatch):
    collector = Collector(CollectorConfig(search_engines=[]))

    async def search_only(topic):
        return SearchResult(warnings=["offline"])

    monkeypatch.setattr(collector, "search_only", search_only)
    result = await collector.run("topic", tmp_path, dry_run=True)
    assert result.warnings == ["offline"]


@pytest.mark.asyncio
async def test_fetch_depth_one_depth_three_skips_and_duplicate(tmp_path, monkeypatch):
    raw_one = tmp_path / "one"
    depth_one = Collector(CollectorConfig(depth=1, min_doc_chars=0))
    result = await depth_one.fetch_and_store(
        "topic",
        [SearchHit(url="https://one.example/a", snippet="search snippet")],
        raw_one,
    )
    assert result.files and "search snippet" in result.files[0].read_text(encoding="utf-8")

    async def fetch(self, url):
        if "bad" in url:
            return FetchResult(url, "", ok=False)
        return FetchResult(url, "long body", ok=True)

    monkeypatch.setattr(Fetcher, "fetch", fetch)
    depth_three = Collector(CollectorConfig(depth=3, min_doc_chars=0))
    good = SearchHit(url="https://three.example/good")

    async def follow(fetched, fetcher, sem, topic):
        return [(good, FetchResult(good.url, "duplicate body", ok=True))]

    monkeypatch.setattr(depth_three, "_follow_links", follow)
    result = await depth_three.fetch_and_store(
        "topic",
        [good, SearchHit(url="https://three.example/bad")],
        tmp_path / "three",
    )
    assert len(result.files) == 1


@pytest.mark.asyncio
async def test_collector_invalid_sources_no_follow_targets_and_module_wrapper(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "sources.json").write_text("not json", encoding="utf-8")
    assert Collector._load_sources(raw) == []

    collector = Collector()
    no_links = await collector._follow_links(
        [(SearchHit(url="https://a.example"), FetchResult("https://a.example", "body"))],
        SimpleNamespace(),
        asyncio.Semaphore(1),
        "topic",
    )
    assert no_links == []

    expected = CollectResult(raw_dir=tmp_path / "wrapped")

    async def run(self, topic, work_dir, *, dry_run=False):
        return expected

    monkeypatch.setattr(Collector, "run", run)
    assert await collector_module.collect("topic", CollectorConfig(), tmp_path) is expected


# ---------------------------------------------------------------------------
# Cleaner


def test_cleaner_helper_boundaries_and_read_failure(tmp_path):
    assert _looks_binary("") is False
    assert _find_content_start(["https://example.com", "short"]) == 0
    assert _shingles("") == set()
    assert _shingles("abc") == {"abc"}
    assert _jaccard(set(), {"x"}) == 0.0
    assert _read_title_excerpt(tmp_path) == (tmp_path.stem, "")


def test_cleaner_process_non_raw_marks_binary_and_short(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "binary.md").write_text("%PDF-1.4 /Filter FlateDecode", encoding="utf-8")
    (incoming / "empty.md").write_text("", encoding="utf-8")

    result = Cleaner(CleanerConfig(min_content_length=20)).process(incoming)

    assert result.clean_dir == tmp_path / "clean"
    assert "binary_or_unparsed_pdf" in result.quality_report["binary"].issues
    assert "too_short" in result.quality_report["binary"].issues
    assert "too_short" in result.quality_report["empty"].issues


class _ScoresLLM:
    def __init__(self, scores=None, error: Exception | None = None):
        self.scores = scores
        self.error = error

    async def chat_structured(self, prompt, schema, system=None):
        if self.error:
            raise self.error
        return schema(scores=self.scores)


@pytest.mark.asyncio
async def test_cleaner_relevance_scores_delete_keep_and_default_missing_score(tmp_path):
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    low = clean_dir / "low.md"
    high = clean_dir / "high.md"
    low.write_text("<!-- title: Low -->\nbody", encoding="utf-8")
    high.write_text("body", encoding="utf-8")
    quality = {
        name: FileQuality(original_size=10, cleaned_size=10, score=1.0)
        for name in ("low", "high")
    }
    result = CleanResult(files=[low, high], quality_report=quality, clean_dir=clean_dir)
    cleaner = Cleaner(
        CleanerConfig(relevance_threshold=0.5, relevance_batch_size=2)
    )

    filtered = await cleaner.filter_relevance(result, _ScoresLLM([0.1]), "topic")

    assert not low.exists()
    assert filtered.files == [high]
    assert filtered.quality_report["low"].issues == ["relevance:0.1", "low_relevance"]


@pytest.mark.asyncio
async def test_cleaner_relevance_non_auth_error_defaults_to_keep(tmp_path):
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    path = clean_dir / "doc.md"
    path.write_text("body", encoding="utf-8")
    result = CleanResult(
        files=[path],
        quality_report={"doc": FileQuality(original_size=4, cleaned_size=4, score=1)},
        clean_dir=clean_dir,
    )

    filtered = await Cleaner().filter_relevance(
        result, _ScoresLLM(error=RuntimeError("temporary")), "topic"
    )

    assert filtered.files == [path]


def test_cleaner_module_wrapper(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "doc.md").write_text("body", encoding="utf-8")
    result = cleaner_module.clean(raw, CleanerConfig(min_content_length=0), tmp_path)
    assert result.clean_dir == tmp_path / "clean"


# ---------------------------------------------------------------------------
# Extractor


def test_extractor_chunk_and_prompt_boundaries():
    assert _chunk("abcdef", 3, 3) == ["abc", "bcd", "cde", "def"]
    prompt = _build_prompt(
        ExtractorConfig(
            tasks=["ner", "re", "triple"],
            entity_types=["Method"],
            relation_types=["uses"],
        ),
        "body",
    )
    assert "Method" in prompt and "uses" in prompt and "triples" in prompt


class _ExtractionLLM:
    def __init__(self, *, fail=False):
        self.fail = fail

    async def chat_structured(self, prompt, schema, system=None):
        if self.fail:
            raise RuntimeError("one bad chunk")
        return _ChunkResult(
            entities=[_LEntity(name="Alpha", type="Method")],
            relations=[_LRelation(subject="Alpha", predicate="uses", object="Beta")],
            triples=[_LTriple(head="Alpha", relation="uses", tail="Beta")],
        )


@pytest.mark.asyncio
async def test_extractor_deduplicates_all_result_types_and_records_line(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.md").write_text("Alpha uses Beta", encoding="utf-8")
    (clean / "b.md").write_text("Alpha uses Beta", encoding="utf-8")

    result = await Extractor().run(clean, _ExtractionLLM())

    assert len(result.entities) == len(result.relations) == len(result.triples) == 1
    assert result.entities[0].source_line == 1


@pytest.mark.asyncio
async def test_extractor_non_auth_chunk_failure_and_module_wrapper(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "bad.md").write_text("some text", encoding="utf-8")
    failed = await Extractor().run(clean, _ExtractionLLM(fail=True), tmp_path / "work")
    assert failed.entities == []

    empty = tmp_path / "empty"
    empty.mkdir()
    wrapped = await extractor_module.extract(empty, ExtractorConfig(), MockLLMClient())
    assert wrapped.output_dir == tmp_path / "extracted"
