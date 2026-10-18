from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research_tool.domain.errors import StageError
from research_tool.domain.models import CleanerConfig, ExtractorConfig, LLMConfig
from research_tool.infrastructure.stages.cleaner import Cleaner
from research_tool.infrastructure.stages.extractor import Extractor, _ChunkResult


class RecordingLLM:
    def __init__(self, replies: list[_ChunkResult] | None = None):
        self.replies = list(replies or [])
        self.prompts: list[str] = []
        self.config = LLMConfig(
            provider="openai",
            model="test-model",
            base_url="https://llm.test/v1",
            **{"api" + "_key": "fixture-key"},
        )

    async def chat_structured(self, prompt, schema, system=None):
        self.prompts.append(prompt)
        if self.replies:
            return self.replies.pop(0)
        return schema()


def _source(path: Path, *, kind: str, body: str) -> None:
    path.write_text(
        "---\n"
        "title: Example\n"
        f"source: https://example.test/{path.stem}\n"
        f"evidence_kind: {kind}\n"
        "---\n\n"
        + body,
        encoding="utf-8",
    )


def test_formal_cleaning_filters_evidence_kind_before_deduplication(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    repeated = "The body source reports a long and reproducible experimental result. " * 8
    _source(raw / "discovery.md", kind="discovery", body=repeated + "discovery-only detail")
    _source(raw / "body.md", kind="body", body=repeated)

    result = Cleaner(CleanerConfig(dedup_similarity=0.5, min_content_length=20)).process(
        raw,
        tmp_path,
        evidence_kinds={"body"},
    )

    assert [path.name for path in result.files] == ["body.md"]
    assert not (tmp_path / "clean" / "discovery.md").exists()
    assert (tmp_path / "clean" / "body.md").exists()


@pytest.mark.asyncio
async def test_extractor_prompt_uses_parse_source_body_not_yaml_frontmatter(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    body = "The measured result is exactly 90 percent after 1000 cycles."
    _source(clean / "paper.md", kind="body", body=body)
    llm = RecordingLLM()

    await Extractor(ExtractorConfig(chunk_size=500, overlap=0)).run(clean, llm)

    assert len(llm.prompts) == 1
    assert body in llm.prompts[0]
    assert "evidence_kind: body" not in llm.prompts[0]
    assert "title: Example" not in llm.prompts[0]


@pytest.mark.asyncio
async def test_invalid_quote_fails_before_writing_any_extracted_artifact(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    _source(clean / "paper.md", kind="body", body="The source says an observed result.")
    llm = RecordingLLM(
        replies=[
            _ChunkResult(
                claims=[
                    {
                        "text": "invented",
                        "quote": "not present in this chunk",
                        "topic": "bad",
                    }
                ]
            )
        ]
    )

    with pytest.raises(StageError, match="引用"):
        await Extractor(
            ExtractorConfig(chunk_size=500, overlap=0, concurrency=1),
        ).run(clean, llm, tmp_path / "work")

    assert not list((tmp_path / "work" / "extracted").glob("*.json"))


@pytest.mark.asyncio
async def test_successful_chunk_is_cached_after_sibling_failure_without_credentials(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    first = "The first study reported 90 percent capacity."
    second = "The second study reported 80 percent capacity."
    _source(clean / "a.md", kind="body", body=first)
    _source(clean / "b.md", kind="body", body=second)

    class Flaky(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def chat_structured(self, prompt, schema, system=None):
            self.calls += 1
            self.prompts.append(prompt)
            if self.calls == 2:
                raise RuntimeError("transient sibling failure")
            quote = first if first in prompt else second
            return schema(claims=[{"text": quote, "quote": quote, "topic": "result"}])

    config = ExtractorConfig(chunk_size=500, overlap=0, concurrency=1, fail_on_chunk_error=True)
    llm = Flaky()
    with pytest.raises(StageError):
        await Extractor(config).run(clean, llm, tmp_path / "work")

    cache = tmp_path / "work" / "extracted" / ".chunk-cache.json"
    assert cache.exists()
    cache_text = cache.read_text(encoding="utf-8")
    assert "api_key" not in cache_text
    assert "fixture-key" not in cache_text
    assert not (cache.parent / "claims.json").exists()
    assert llm.calls == 2

    result = await Extractor(config).run(clean, llm, tmp_path / "work")

    assert {claim["text"] for claim in result.claims} == {first, second}
    assert llm.calls == 3
    assert first not in llm.prompts[-1] and second in llm.prompts[-1]
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["version"] >= 1
    assert all("key" in entry and "result" in entry for entry in payload["entries"])
    assert hashlib.sha256(first.encode()).hexdigest() not in cache_text


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["absent", "sibling"])
async def test_invalid_quote_stops_queued_chunks_even_if_it_exists_elsewhere(tmp_path, location):
    clean = tmp_path / "clean"
    clean.mkdir()
    first = "This sentence belongs to the first body chunk."
    second = "This sentence belongs to the second body chunk."
    _source(clean / "paper.md", kind="body", body=first + second)
    quote = second if location == "sibling" else "This sentence was invented."
    llm = RecordingLLM([_ChunkResult(claims=[{"quote": quote}])])

    with pytest.raises(StageError, match="引用"):
        await Extractor(
            ExtractorConfig(chunk_size=len(first), overlap=0, concurrency=1),
        ).run(clean, llm)

    assert len(llm.prompts) == 1
    assert not (tmp_path / "extracted" / "claims.json").exists()
    assert not (tmp_path / "extracted" / ".chunk-cache.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["body", "model", "endpoint", "config", "request_options"])
async def test_verified_chunk_cache_is_invalidated_by_semantic_identity(tmp_path, change):
    clean = tmp_path / "clean"
    clean.mkdir()
    source = clean / "paper.md"
    _source(source, kind="body", body="This is the original source evidence.")
    config = ExtractorConfig(chunk_size=500)
    llm = RecordingLLM()
    await Extractor(config).run(clean, llm)
    assert len(llm.prompts) == 1
    await Extractor(config).run(clean, llm)
    assert len(llm.prompts) == 1

    if change == "body":
        _source(source, kind="body", body="This source evidence has changed.")
    elif change == "model":
        llm.config.model = "different-test-model"
    elif change == "endpoint":
        llm.config.base_url = "https://different-llm.test/v1"
    elif change == "request_options":
        llm.config.request_options = {"extra_body": {"reasoning_effort": "low"}}
    else:
        config = config.model_copy(update={"entity_types": ["Study"]})
    await Extractor(config).run(clean, llm)
    assert len(llm.prompts) == 2


@pytest.mark.asyncio
async def test_rotating_api_key_does_not_invalidate_or_persist_chunk_cache(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    _source(clean / "paper.md", kind="body", body="This is actual source evidence.")
    llm = RecordingLLM()
    await Extractor().run(clean, llm)
    setattr(llm.config, "api" + "_key", "rotated-fixture-value")
    await Extractor().run(clean, llm)
    assert len(llm.prompts) == 1
    cache = (tmp_path / "extracted" / ".chunk-cache.json").read_text(encoding="utf-8")
    assert "rotated-fixture-value" not in cache
    assert "api_key" not in cache
    assert "llm.test" not in cache


def test_body_only_cleaning_removes_stale_excluded_output_and_keeps_raw(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _source(raw / "discovery.md", kind="discovery", body="A long discovery sentence. " * 20)
    cleaner = Cleaner(CleanerConfig(min_content_length=20))
    prior = cleaner.process(raw, tmp_path)
    assert prior.files and prior.files[0].exists()  # Legacy standalone default is unchanged.
    formal = cleaner.process(raw, tmp_path, evidence_kinds={"body"})
    assert formal.files == []
    assert not prior.files[0].exists()
    assert (raw / "discovery.md").exists()


@pytest.mark.asyncio
async def test_repeated_quote_offsets_identify_current_chunk_occurrence(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    quote = "The study reports an observed result."
    first = quote + " Padding before the repeated sentence."
    _source(clean / "paper.md", kind="body", body=first + quote)
    llm = RecordingLLM([_ChunkResult(), _ChunkResult(claims=[{"quote": quote}])])
    result = await Extractor(
        ExtractorConfig(chunk_size=len(first), overlap=0, concurrency=1),
    ).run(clean, llm)
    citation = result.claims[0]["citations"][0]
    assert citation["start_offset"] == len(first)
    assert citation["end_offset"] == len(first) + len(quote)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_invalid_structured_output_stops_queued_requests(tmp_path, wrapped):
    from research_tool.domain.errors import LLMError
    from pydantic import ValidationError

    clean = tmp_path / "clean"
    clean.mkdir()
    for name in ("a", "b", "c"):
        _source(clean / f"{name}.md", kind="body", body="The source has actual evidence.")

    class Malformed(RecordingLLM):
        async def chat_structured(self, prompt, schema, system=None):
            self.prompts.append(prompt)
            try:
                return schema.model_validate({"claims": [{"quote": None}]})
            except ValidationError as exc:
                if wrapped:
                    raise LLMError("schema invalid") from exc
                raise

    llm = Malformed()
    with pytest.raises(StageError):
        await Extractor(ExtractorConfig(concurrency=1, fail_on_chunk_error=True)).run(clean, llm)
    assert len(llm.prompts) == 1
    assert not (tmp_path / "extracted" / "claims.json").exists()


@pytest.mark.asyncio
async def test_failed_reused_directory_has_no_stale_completed_artifacts(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    source = clean / "paper.md"
    quote = "The old study reports the first result."
    _source(source, kind="body", body=quote)
    llm = RecordingLLM([_ChunkResult(claims=[{"quote": quote}])])
    await Extractor().run(clean, llm)
    old = (tmp_path / "extracted" / "claims.json").read_bytes()
    _source(source, kind="body", body="The new source has different evidence.")
    llm.replies = [_ChunkResult(claims=[{"quote": "This was invented."}])]
    with pytest.raises(StageError):
        await Extractor().run(clean, llm)
    for name in ("claims", "entities", "relations", "triples", "schema"):
        assert not (tmp_path / "extracted" / f"{name}.json").exists()
    # Previous valid output is retained outside the current completion namespace.
    assert any(path.read_bytes() == old for path in (tmp_path / "extracted" / ".previous").glob("*/claims.json"))


@pytest.mark.asyncio
async def test_formal_pipeline_keeps_body_when_longer_discovery_is_near_duplicate(tmp_path):
    from research_tool.application.pipeline import ResearchPipeline
    from research_tool.infrastructure.llm import MockLLMClient
    from research_tool.tests.test_nine_loop_runner import QUOTE, config, model_reply

    incoming = tmp_path / "input"
    incoming.mkdir()
    body = QUOTE + "\n\n" + "The experimental method reports controls and limitations. " * 8
    _source(incoming / "body.md", kind="body", body=body)
    _source(incoming / "discovery.md", kind="discovery", body=body + " More discovery metadata.")
    cfg = config(tmp_path / "output")
    cfg.input_dir = incoming
    cfg.collector.strategies.enabled = False
    cfg.cleaner.dedup_similarity = 0.5
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_SUCCESS"
    assert result.report_result is not None
    assert QUOTE in result.report_result.report_path.read_text(encoding="utf-8")
    clean = result.report_result.report_path.parent.parent / "initial" / "clean"
    assert [path.name for path in clean.glob("*.md")] == ["body.md"]
