"""Behavior and defensive-path coverage for the deepen stage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from research_tool.domain.errors import LLMAuthenticationError
from research_tool.domain.models import DeepenConfig
from research_tool.infrastructure.search.base import SearchResult
from research_tool.infrastructure.stages.deepen import (
    DeepenStage,
    _Profile,
    _TimelineNode,
    deepen,
)


class ScriptLLM:
    def __init__(self, handler=None) -> None:
        self.handler = handler or (lambda prompt, schema: schema())
        self.prompts: list[str] = []

    async def chat_structured(self, prompt, schema, system=None):
        self.prompts.append(prompt)
        result = self.handler(prompt, schema)
        if isinstance(result, BaseException):
            raise result
        return result


class StubCollector:
    def __init__(self) -> None:
        self.search_calls: list[tuple[list[str], dict]] = []
        self.fetch_calls: list[tuple[str, list, Path]] = []
        self.search_warnings = ["search warning"]
        self.fetch_warnings = ["fetch warning"]
        self.files: list[Path] = []

    async def search_queries(self, queries, **kwargs):
        self.search_calls.append((list(queries), kwargs))
        return SearchResult(hits=[], warnings=list(self.search_warnings))

    async def fetch_and_store(self, topic, hits, raw_dir):
        self.fetch_calls.append((topic, list(hits), Path(raw_dir)))
        return SimpleNamespace(files=list(self.files), warnings=list(self.fetch_warnings))


def make_stage(config: DeepenConfig | None = None, llm=None, collector=None) -> DeepenStage:
    return DeepenStage(config or DeepenConfig(), collector or StubCollector(), llm or ScriptLLM())


def seed_raw(raw: Path, name: str = "01.md", body: str = "useful body") -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    path.write_text(f"<!-- title: Seed -->\n<!-- source: https://example.test -->\n{body}", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_run_profile_iteration_timeline_disambiguation_and_confidence_stop(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    seed_raw(raw)
    collector = StubCollector()
    stage = make_stage(
        DeepenConfig(
            entity_split=False,
            breadth=2,
            profile_iterations=3,
            min_new_files_per_iter=0,
            min_profile_confidence=0.8,
            gap_detection=False,
            contradiction_check=False,
        ),
        collector=collector,
    )
    initial = _Profile(
        name="Ada",
        institutions=["Lab"],
        timeline=[_TimelineNode(period_from=2020, period_to=2021, institution="Lab")],
        confidence=0.5,
    )
    refreshed = initial.model_copy(update={"confidence": 0.9})
    profiles = iter([initial, refreshed])
    monkeypatch.setattr(stage, "_extract_profile", lambda *a, **k: anext_profile(profiles))

    timeline_file = raw / "timeline.md"

    async def fake_timeline(*args):
        return [timeline_file], ["timeline warning"]

    async def fake_disambiguate(*args):
        return 1

    monkeypatch.setattr(stage, "_timeline_search", fake_timeline)
    monkeypatch.setattr(stage, "_disambiguate", fake_disambiguate)

    result = await stage.run("Ada", raw)

    assert timeline_file in result.new_files
    assert "timeline warning" in result.warnings
    assert any("\u6d88\u6b67\u79fb\u8d70 1" in warning for warning in result.warnings)
    assert len(collector.search_calls) == 2  # entity query + profile query


async def anext_profile(iterator):
    return next(iterator)


@pytest.mark.asyncio
async def test_run_stops_profile_iteration_when_profile_missing_and_gap_missing(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stage = make_stage(
        DeepenConfig(
            entity_split=False,
            profile_iterations=2,
            depth=2,
            gap_detection=True,
            contradiction_check=False,
        )
    )

    result = await stage.run("topic", raw)

    assert result.entities == []
    assert (raw / ".deepen_done").exists()


@pytest.mark.asyncio
async def test_run_profile_iteration_stops_when_too_few_new_files(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    seed_raw(raw)
    stage = make_stage(
        DeepenConfig(
            entity_split=False,
            profile_iterations=2,
            timeline_backtrack=False,
            disambiguation=False,
            min_new_files_per_iter=1,
            gap_detection=False,
            contradiction_check=False,
        )
    )
    profile = _Profile(name="Ada", institutions=["Lab"], confidence=0.2)

    async def fake_profile(*args):
        return profile

    monkeypatch.setattr(stage, "_extract_profile", fake_profile)
    result = await stage.run("Ada", raw)
    assert result.queries


@pytest.mark.asyncio
async def test_split_entities_normalizes_deduplicates_and_falls_back():
    llm = ScriptLLM(lambda p, s: s(entities=[" A ", "", "A", "B", "C"]))
    stage = make_stage(DeepenConfig(max_entities=2), llm=llm)
    assert await stage._split_entities("topic") == ["A", "B"]

    empty = make_stage(llm=ScriptLLM(lambda p, s: s(entities=[])))
    assert await empty._split_entities("topic") == ["topic"]

    disabled = make_stage(DeepenConfig(entity_split=False))
    assert await disabled._split_entities("topic") == ["topic"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("boom"), LLMAuthenticationError("401")])
async def test_split_entities_error_policy(error):
    stage = make_stage(llm=ScriptLLM(lambda p, s: error))
    if isinstance(error, LLMAuthenticationError):
        with pytest.raises(LLMAuthenticationError):
            await stage._split_entities("topic")
    else:
        assert await stage._split_entities("topic") == ["topic"]


@pytest.mark.asyncio
async def test_entity_queries_success_empty_and_error_policy():
    success = make_stage(llm=ScriptLLM(lambda p, s: s(queries=[" Q ", ""])))
    assert await success._entity_queries("topic", ["A", "B"]) == ["Q"]

    empty = make_stage(llm=ScriptLLM(lambda p, s: s(queries=[])))
    assert await empty._entity_queries("topic", ["A"]) == ["A"]

    fallback = make_stage(llm=ScriptLLM(lambda p, s: RuntimeError("boom")))
    assert await fallback._entity_queries("topic", ["A", "B"]) == ["A", "B", "A \u8bba\u6587", "B \u8bba\u6587"]

    auth = make_stage(llm=ScriptLLM(lambda p, s: LLMAuthenticationError("401")))
    with pytest.raises(LLMAuthenticationError):
        await auth._entity_queries("topic", ["A"])


@pytest.mark.asyncio
async def test_extract_profile_empty_invalid_valid_and_auth(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stage = make_stage()
    assert await stage._extract_profile("topic", raw, None) is None

    seed_raw(raw)
    stage.llm = ScriptLLM(lambda p, s: RuntimeError("boom"))
    assert await stage._extract_profile("topic", raw, "focus") is None

    stage.llm = ScriptLLM(lambda p, s: s(name="Ada"))
    assert await stage._extract_profile("topic", raw, None) is None

    stage.llm = ScriptLLM(lambda p, s: s(name="Ada", fields=["AI"]))
    profile = await stage._extract_profile("topic", raw, "focus")
    assert profile is not None and profile.fields == ["AI"]
    assert "\u6838\u5fc3\u5b9e\u4f53\uff1a\u300cfocus\u300d" in stage.llm.prompts[-1]

    stage.llm = ScriptLLM(lambda p, s: LLMAuthenticationError("401"))
    with pytest.raises(LLMAuthenticationError):
        await stage._extract_profile("topic", raw, None)


@pytest.mark.asyncio
async def test_timeline_search_early_returns_skips_blank_and_propagates_warnings(tmp_path):
    raw = tmp_path / "raw"
    collector = StubCollector()
    added = raw / "added.md"
    collector.files = [added]
    stage = make_stage(DeepenConfig(breadth=2), collector=collector)

    assert await stage._timeline_search(_Profile(), "topic", raw) == ([], [])
    assert await stage._timeline_search(_Profile(name="Ada"), "topic", raw) == ([], [])

    profile = _Profile(
        name="Ada",
        name_en="Ada E",
        timeline=[
            _TimelineNode(institution="  "),
            _TimelineNode(period_from=2019, period_to=2022, institution=" Lab "),
        ],
    )
    files, warnings = await stage._timeline_search(profile, "topic", raw)
    assert files == [added]
    assert warnings == ["search warning", "fetch warning"]
    assert collector.search_calls == [
        (["Ada E Lab"], {"from_year": 2019, "to_year": 2022, "sort": None, "offset": 0})
    ]


@pytest.mark.asyncio
async def test_disambiguate_guard_errors_and_replaces_existing_target(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stage = make_stage()
    assert await stage._disambiguate(raw, _Profile(confidence=0.69), "t") == 0
    assert await stage._disambiguate(raw, _Profile(confidence=0.9), "t") == 0
    assert await stage._disambiguate(
        raw, _Profile(name="Ada", fields=["AI"], confidence=0.9), "t"
    ) == 0

    source = seed_raw(raw, "01.md")
    stage.llm = ScriptLLM(lambda p, s: RuntimeError("boom"))
    assert await stage._disambiguate(
        raw, _Profile(name="Ada", institutions=["Lab"], confidence=0.9), "t"
    ) == 0
    assert source.exists()

    stage.llm = ScriptLLM(lambda p, s: LLMAuthenticationError("401"))
    with pytest.raises(LLMAuthenticationError):
        await stage._disambiguate(
            raw, _Profile(name="Ada", fields=["AI"], confidence=0.9), "t"
        )

    bucket = raw / "_disambig"
    bucket.mkdir()
    target = bucket / source.name
    target.write_text("old", encoding="utf-8")
    stage.llm = ScriptLLM(lambda p, s: s(owns=[False]))
    moved = await stage._disambiguate(
        raw, _Profile(name="Ada", fields=["AI"], confidence=0.9), "t"
    )
    assert moved == 1
    assert not source.exists()
    assert target.read_text(encoding="utf-8").startswith("<!-- title: Seed -->")


def test_profile_queries_cover_anchor_and_alias_only_paths():
    stage = make_stage()
    profile = _Profile(
        name="Ada",
        name_en="Ada E",
        aliases=["A. E."],
        institutions=["L1", "L2", "L3", "L4"],
        fields=["F1"],
        keywords=["K1", "K2", "K3", "K4"],
    )
    queries = stage._profile_queries(profile)
    assert queries[0:2] == ["Ada E", "Ada"]
    assert "Ada E L4" not in queries
    assert "Ada E K4" not in queries
    assert stage._profile_queries(_Profile(aliases=[" alias ", "alias"])) == ["alias"]


@pytest.mark.asyncio
async def test_gap_contradiction_and_safe_query_policies(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stage = make_stage()
    assert await stage._detect_gaps("t", raw) == []
    assert await stage._detect_contradictions("t", raw) == []

    seed_raw(raw)
    stage.llm = ScriptLLM(lambda p, s: s(queries=[" Q ", ""]))
    assert await stage._detect_gaps("t", raw) == ["Q"]
    assert await stage._detect_contradictions("t", raw) == ["Q"]

    stage.llm = ScriptLLM(lambda p, s: RuntimeError("boom"))
    assert await stage._safe_queries("p") == []

    stage.llm = ScriptLLM(lambda p, s: LLMAuthenticationError("401"))
    with pytest.raises(LLMAuthenticationError):
        await stage._safe_queries("p")


def test_read_digest_skips_oserror_uses_filename_and_stops_at_total_limit(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    bad = seed_raw(raw, "01-bad.md")
    seed_raw(raw, "02-plain.md", "plain body")
    seed_raw(raw, "03-overflow.md", "overflow")
    original = Path.read_text

    def fake_read(path, *args, **kwargs):
        if path == bad:
            raise OSError("unreadable")
        text = original(path, *args, **kwargs)
        return text.replace("<!-- title: Seed -->\n", "")

    monkeypatch.setattr(Path, "read_text", fake_read)
    stage = make_stage(DeepenConfig(per_file_chars=20, max_input_chars=35))
    digest = stage._read_digest(raw)
    assert "02-plain" in digest
    assert "03-overflow" not in digest


@pytest.mark.asyncio
async def test_module_level_deepen_delegates_and_collects_warnings(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    collector = StubCollector()
    result = await deepen(
        "topic",
        DeepenConfig(
            entity_split=False,
            profile_extract=False,
            gap_detection=False,
            contradiction_check=False,
        ),
        collector,
        ScriptLLM(lambda p, s: s(queries=["q"])),
        raw,
        core_keyword="focus",
    )
    assert result.queries == ["q"]
    assert result.warnings == ["search warning", "fetch warning"]
