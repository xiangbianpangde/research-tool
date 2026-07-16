"""Source-level observability for real, multi-backend collection runs."""

from __future__ import annotations

import json

import pytest

from research_tool.domain.models import CollectorConfig, SourceAudit
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages import collector as collector_module
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.fetcher import FetchResult
from research_tool.infrastructure.stages.reporter import Reporter
from research_tool.infrastructure.llm import MockLLMClient


class _Backend:
    def __init__(self, results):
        self._results = results

    async def search(self, query, max_results, language="both", **kwargs):
        result = self._results[query]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_search_audit_counts_attempts_hits_failures_filtering_and_dedup(monkeypatch):
    backends = {
        "web": _Backend(
            {
                "alpha": [
                    SearchHit(url="https://example.com/a", title="alpha", source_engine="web"),
                    SearchHit(url="https://example.com/noise", title="noise", source_engine="web"),
                ],
                "beta": [
                    SearchHit(url="https://example.com/a", title="beta", source_engine="web")
                ],
            }
        ),
        "pubmed": _Backend({"alpha": RuntimeError("rate limited"), "beta": []}),
    }
    monkeypatch.setattr(
        collector_module,
        "get_backend",
        lambda name, config: backends[str(name)],
    )
    collector = Collector(
        CollectorConfig(
            search_engines=["web", "pubmed"],
            max_total_results=20,
            search_relevance_min_overlap=0.5,
        )
    )

    result = await collector.search_queries(["alpha", "beta"])
    audits = {audit.engine: audit for audit in result.source_audits}

    assert audits["web"] == SourceAudit(
        engine="web", attempted=2, hits=3, filtered=1, deduplicated=1
    )
    assert audits["pubmed"] == SourceAudit(engine="pubmed", attempted=2, failed=1)


@pytest.mark.asyncio
async def test_fetch_audit_is_persisted_and_distinguishes_retained_from_failed(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    collector = Collector(CollectorConfig(min_doc_chars=10, depth=2))
    hits = [
        SearchHit(url="https://example.com/good", title="good", source_engine="web"),
        SearchHit(url="https://example.com/fail", title="fail", source_engine="web"),
        SearchHit(url="https://example.com/short", title="short", source_engine="pubmed"),
    ]

    async def fake_fetch(self, url):
        if url.endswith("good"):
            return FetchResult(url, "substantial content", ok=True)
        if url.endswith("short"):
            return FetchResult(url, "tiny", ok=True)
        return FetchResult(url, "", ok=False, error="blocked")

    monkeypatch.setattr(collector_module.Fetcher, "fetch", fake_fetch)
    result = await collector.fetch_and_store("topic", hits, raw_dir)
    audits = {audit.engine: audit for audit in result.source_audits}

    assert audits["web"].fetch_failed == 1
    assert audits["web"].retained == 1
    assert audits["pubmed"].filtered == 1
    assert audits["pubmed"].retained == 0

    payload = json.loads((raw_dir / "source-audit.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert {item["engine"] for item in payload["sources"]} == {"web", "pubmed"}


@pytest.mark.asyncio
async def test_report_includes_source_funnel_and_zero_contribution_engine(tmp_path):
    tree = tmp_path / "tree"
    raw = tmp_path / "raw"
    tree.mkdir()
    raw.mkdir()
    (tree / "00-主表.md").write_text("# tree", encoding="utf-8")
    (raw / "sources.json").write_text("[]", encoding="utf-8")
    (raw / "source-audit.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    SourceAudit(engine="web", attempted=2, hits=3, retained=1).model_dump(),
                    SourceAudit(engine="pubmed", attempted=2, failed=2).model_dump(),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = await Reporter().run(tree, MockLLMClient(chat_response="body"), topic="topic")
    report = result.report_path.read_text(encoding="utf-8")

    assert "## 来源覆盖审计" in report
    assert "| pubmed | 2 | 0 | 2 | 0 | 0 | 0 | 0 |" in report
    assert "已调用后端: 2 个；有最终贡献: 1 个" in report
