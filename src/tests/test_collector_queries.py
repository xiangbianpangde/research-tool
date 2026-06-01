import pytest

from src.infrastructure.llm import MockLLMClient
from src.domain.models import CollectorConfig
from src.infrastructure.stages.collector import (
    Collector,
    _build_queries,
    _llm_build_queries,
)


def test_template_rounds():
    assert _build_queries("X", 1, "both") == ["X"]
    q2 = _build_queries("X", 2, "en")
    assert "X overview" in q2 and "X survey" in q2
    assert len(_build_queries("X", 3, "both")) == 1 + 2 * 3 + 2 * 3


@pytest.mark.asyncio
async def test_llm_queries_used_when_enabled():
    def structured(prompt, schema):
        return schema(queries=["X 原理", "X applications", "X vs Y"])

    llm = MockLLMClient(structured_response=structured)
    qs = await _llm_build_queries("X", 2, "both", llm)
    assert qs[0] == "X"  # 始终含核心词
    assert "X applications" in qs
    assert len(qs) == len(set(qs))  # 去重


@pytest.mark.asyncio
async def test_llm_falls_back_on_error():
    class Boom(MockLLMClient):
        async def chat_structured(self, *a, **k):
            raise RuntimeError("boom")

    qs = await _llm_build_queries("X", 2, "en", Boom())
    assert "X overview" in qs  # 回退到模板


@pytest.mark.asyncio
async def test_collector_uses_template_without_llm(monkeypatch):
    cfg = CollectorConfig(search_rounds=2, language="en", llm_query_expansion=True)
    c = Collector(cfg, llm=None)  # 开关开但无 llm → 走模板

    captured = {}

    async def fake_search(self, query, max_results, language, *, from_year=None, to_year=None, sort=None, offset=0):
        captured.setdefault("queries", []).append(query)
        return []

    monkeypatch.setattr(
        "src.infrastructure.search.duckduckgo.DuckDuckGoBackend.search", fake_search
    )
    await c.search_only("X")
    assert "X overview" in captured["queries"]
