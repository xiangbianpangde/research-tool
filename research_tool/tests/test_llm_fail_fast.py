from __future__ import annotations

import asyncio

import pytest

from research_tool.domain.errors import LLMAuthenticationError, LLMError, classify_llm_error
from research_tool.domain.models import ExtractorConfig
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.stages.collector import Collector, _llm_build_queries
from research_tool.infrastructure.stages.research_strategies import ResearchStrategies
from research_tool.infrastructure.stages.extractor import Extractor
from research_tool.infrastructure.stages.cleaner import Cleaner
from research_tool.infrastructure.stages.organizer import Organizer, _Node, _NodePlan, _SourceDoc
from research_tool.common.translate import translate_markdown
from research_tool.domain.models import CleanerConfig, CollectionStrategyConfig


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("provider request failed")


def test_classify_401_as_authentication_error_without_leaking_provider_body():
    original = _StatusError(401)

    classified = classify_llm_error("chat", original)

    assert isinstance(classified, LLMAuthenticationError)
    assert classified.__cause__ is original
    assert "provider request failed" not in str(classified)


def test_classify_non_401_as_regular_llm_error():
    assert type(classify_llm_error("chat", _StatusError(500))) is LLMError


class _AuthFailLLM(MockLLMClient):
    async def chat(self, prompt, system=None, temperature=None):
        raise LLMAuthenticationError("LLM 鉴权失败")

    async def chat_structured(self, prompt, schema, system=None):
        raise LLMAuthenticationError("LLM 鉴权失败")


@pytest.mark.asyncio
async def test_collector_query_expansion_does_not_fallback_on_401():
    with pytest.raises(LLMAuthenticationError):
        await _llm_build_queries("paper", 1, "en", _AuthFailLLM())


@pytest.mark.asyncio
async def test_collection_strategies_propagate_401_without_legacy_marker(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    collector = Collector()
    strategies = ResearchStrategies(CollectionStrategyConfig(), collector, _AuthFailLLM())

    with pytest.raises(LLMAuthenticationError):
        await strategies.collect_more("paper", raw)

    assert not (raw / ".deepen_done").exists()


@pytest.mark.asyncio
async def test_extractor_does_not_emit_empty_success_after_401(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "01.md").write_text("A sufficiently long paper body.", encoding="utf-8")

    with pytest.raises(LLMAuthenticationError):
        await Extractor(ExtractorConfig()).run(clean, _AuthFailLLM())

    assert not (tmp_path / "extracted" / "entities.json").exists()


@pytest.mark.asyncio
async def test_cleaner_relevance_filter_does_not_default_pass_on_401(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "01.md").write_text("Relevant paper body " * 30, encoding="utf-8")
    cleaner = Cleaner(CleanerConfig(min_content_length=20, relevance_filter=True))
    clean_result = cleaner.process(raw)

    with pytest.raises(LLMAuthenticationError):
        await cleaner.filter_relevance(clean_result, _AuthFailLLM(), "paper")


@pytest.mark.asyncio
async def test_organizer_summary_does_not_drop_source_on_401():
    source = _SourceDoc(sid="01", title="Paper", url="https://example.com", text="body")

    with pytest.raises(LLMAuthenticationError):
        await Organizer()._summarize_doc("paper", source, _AuthFailLLM())


@pytest.mark.asyncio
async def test_organizer_cancels_sibling_node_requests_after_401(tmp_path, monkeypatch):
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "entities.json").write_text("[]", encoding="utf-8")
    organizer = Organizer()
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def fake_plan(topic, evidence, llm):
        return _NodePlan(
            nodes=[_Node(title="first"), _Node(title="second")],
            main_thread="test",
        )

    async def fake_body(index, node, node_titles, evidence, llm):
        if index == 1:
            await sibling_started.wait()
            raise LLMAuthenticationError("LLM 鉴权失败")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    monkeypatch.setattr(organizer, "_plan_nodes", fake_plan)
    monkeypatch.setattr(organizer, "_node_body", fake_body)

    with pytest.raises(LLMAuthenticationError):
        await organizer.run(extracted, _AuthFailLLM(), tmp_path)

    await asyncio.sleep(0)
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_translate_does_not_return_original_text_on_401():
    with pytest.raises(LLMAuthenticationError):
        await translate_markdown("English paper text", _AuthFailLLM())
