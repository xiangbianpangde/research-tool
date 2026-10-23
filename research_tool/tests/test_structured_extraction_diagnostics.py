"""Structured-response failures must be strict, actionable and safe to persist."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import LLMError, StageError
from research_tool.domain.models import ExtractorConfig, LLMConfig, PipelineConfig
from research_tool.infrastructure.stages.extractor import Extractor, _ChunkResult, _digest
from research_tool.nine_loop.evidence import parse_source
from research_tool.tests.test_llm_portability import openai_client


class Reply(BaseModel):
    value: int


def response(content, finish_reason="stop", **message_fields):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, **message_fields),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(
            prompt_tokens=120, completion_tokens=80, total_tokens=200,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=60),
        ),
    )


def compatible(client):
    client.config = LLMConfig(
        provider="openai_compatible", model="fixture-model",
        base_url="https://fixture.invalid/v1", api_key="".join(("credential", "-", "canary")),
    )
    return client


def source_dir(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "paper.md").write_text(
        "<!-- source: https://papers.invalid/study -->\n"
        "<!-- title: Observational study -->\n\n"
        "Nineteen of 89 deliveries were complicated by postpartum hemorrhage.",
        encoding="utf-8",
    )
    return clean


def diagnostic(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(error, "diagnostic", None) is not None:
            return error.diagnostic.model_dump(mode="json", exclude_none=True)
        error = error.__cause__ or error.__context__
    pytest.fail("failure has no typed safe diagnostic")


@pytest.mark.asyncio
@pytest.mark.parametrize(("finish_reason", "reason"), [
    ("length", "truncated"), ("content_filter", "content_filter"),
    ("tool_calls", "unexpected_finish"), ("function_call", "unexpected_finish"),
    ("provider-secret-canary", "unexpected_finish"),
])
async def test_non_normal_completion_is_rejected_even_when_json_is_valid(finish_reason, reason):
    client, calls = openai_client([response('{"value":7}', finish_reason)])

    with pytest.raises(LLMError) as caught:
        await client.chat_structured("extract", Reply)

    detail = diagnostic(caught.value)
    assert detail["reason"] == reason
    assert detail["finish_reason"] == (finish_reason if finish_reason != "provider-secret-canary" else "other")
    assert detail["completion_tokens"] == 80
    assert detail["reasoning_tokens"] == 60
    assert len(calls.calls) == 1  # no silent second request or JSON-mode downgrade
    assert "provider-secret-canary" not in json.dumps(detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", " \n\t", "<think>private-reasoning-canary</think>"])
async def test_empty_visible_content_never_becomes_a_default_object(content):
    client, _ = openai_client([response(content)])

    with pytest.raises(LLMError) as caught:
        await client.chat_structured("extract", _ChunkResult)

    detail = diagnostic(caught.value)
    assert detail["reason"] == "empty_content"
    assert "private-reasoning-canary" not in json.dumps(detail)


@pytest.mark.asyncio
async def test_missing_choices_is_a_classified_failure_not_an_index_error():
    client, _ = openai_client([SimpleNamespace(choices=[], usage=None)])

    with pytest.raises(LLMError) as caught:
        await client.chat_structured("extract", Reply)

    assert diagnostic(caught.value)["reason"] == "missing_choices"


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "reason"), [
    ('{"value":', "json_invalid"),
    ('{"value":"response-secret-canary"}', "schema_invalid"),
])
async def test_parser_failures_keep_locations_and_metadata_without_model_text(content, reason):
    client, _ = openai_client([response(content)])

    with pytest.raises(LLMError) as caught:
        await client.chat_structured("extract", Reply)

    detail = diagnostic(caught.value)
    assert detail["reason"] == reason
    assert detail["content_chars"] == len(content)
    assert len(detail["content_sha256"]) == 64
    assert detail["prompt_tokens"] == 120
    if reason == "json_invalid":
        assert detail["json_line"] == 1
        assert detail["json_column"] > 0
    else:
        assert detail["schema_fields"] == ["value"]
    assert "response-secret-canary" not in str(caught.value) + json.dumps(detail)


@pytest.mark.asyncio
async def test_untrusted_dictionary_keys_are_not_written_into_schema_diagnostics():
    client, _ = openai_client([response(
        '{"claims":[{"credential-canary":{"nested":"private"}}]}'
    )])

    with pytest.raises(LLMError) as caught:
        await client.chat_structured("extract", _ChunkResult)

    detail = diagnostic(caught.value)
    assert detail["reason"] == "schema_invalid"
    assert detail["schema_fields"]
    assert "credential-canary" not in json.dumps(detail)
    assert "nested" not in json.dumps(detail)


@pytest.mark.asyncio
async def test_normal_completion_stays_compatible_and_explicit_empty_object_is_schema_owned():
    client, _ = openai_client([response('{"value":7}'), response("{}")])

    assert (await client.chat_structured("extract", Reply)).value == 7
    assert (await client.chat_structured("schema legitimately allows defaults", _ChunkResult)).claims == []


@pytest.mark.asyncio
async def test_extractor_never_retries_an_explicit_content_filter(tmp_path):
    clean = source_dir(tmp_path)
    client, calls = openai_client([
        response(None, "content_filter"), response(None, "content_filter"),
    ])
    compatible(client)

    with pytest.raises(StageError) as caught:
        await Extractor(ExtractorConfig(fail_on_chunk_error=True)).run(clean, client)

    assert diagnostic(caught.value)["reason"] == "content_filter"
    assert len(calls.calls) == 1
    assert not (tmp_path / "extracted/claims.json").exists()


@pytest.mark.asyncio
async def test_full_extraction_rejects_repeated_empty_results_without_caching_success(tmp_path):
    clean = source_dir(tmp_path)
    client, calls = openai_client([response("{}"), response("{}")])
    compatible(client)

    with pytest.raises(StageError) as caught:
        await Extractor(ExtractorConfig(fail_on_chunk_error=True)).run(clean, client)

    detail = diagnostic(caught.value)
    assert detail["reason"] == "empty_extraction"
    assert detail["chunk_index"] == 0
    assert detail["chunk_offset"] == 0
    assert detail["source_id"] == parse_source((clean / "paper.md").read_text())["source_id"]
    assert len(calls.calls) == 2
    assert not (tmp_path / "extracted/claims.json").exists()
    cache = tmp_path / "extracted/.chunk-cache.json"
    assert not cache.exists() or not json.loads(cache.read_text())["entries"]


@pytest.mark.asyncio
async def test_empty_cache_with_valid_digest_is_not_reused_as_completed_evidence(tmp_path):
    clean = source_dir(tmp_path)
    sentence = parse_source((clean / "paper.md").read_text())["text"]
    good = json.dumps({"claims": [{"text": sentence, "quote": sentence, "topic": "risk"}]})
    client, calls = openai_client([response(good), response("{}"), response("{}")])
    compatible(client)
    extractor = Extractor(ExtractorConfig(fail_on_chunk_error=True))
    await extractor.run(clean, client)
    cache = tmp_path / "extracted/.chunk-cache.json"
    payload = json.loads(cache.read_text())
    payload["entries"][0]["result"] = _ChunkResult().model_dump()
    payload["entries"][0]["result_sha256"] = _digest(payload["entries"][0]["result"])
    cache.write_text(json.dumps(payload))

    with pytest.raises(StageError):
        await extractor.run(clean, client)

    assert len(calls.calls) == 3
    assert not (tmp_path / "extracted/claims.json").exists()


@pytest.mark.asyncio
async def test_pipeline_persists_safe_block_diagnostic_in_summary_record_and_event(tmp_path):
    clean = source_dir(tmp_path)
    client, _ = openai_client([
        response("PONG"),
        response('{"entities":[{"name":"response-secret-canary","confidence":"bad"}]}'),
        response('{"entities":[{"name":"response-secret-canary","confidence":"bad"}]}'),
    ])
    compatible(client)
    config = PipelineConfig(
        topic="risk", work_dir=tmp_path / "output", input_dir=clean,
        llm=client.config, llm_stage_attempts=1, llm_retry_backoff_sec=0,
    )
    config.collector.strategies.enabled = False
    config.cleaner.min_content_length = 20
    pipeline = ResearchPipeline(config, llm=client)

    events = [event async for event in pipeline.stream()]

    result = pipeline._result
    summary = json.loads(result.run_summary_path.read_text())
    detail = summary["stage_metrics"][-1].get("diagnostic")
    assert detail is not None
    assert detail["reason"] == "schema_invalid"
    assert detail["schema_fields"] == ["entities.0.confidence"]
    assert detail["finish_reason"] == "stop"
    assert detail["completion_tokens"] == 80
    assert detail["chunk_index"] == 0
    record = json.loads((result.topic_dir / summary["record_path"]).read_text())
    # The canonical record has an exact external reader contract. Keep diagnostics
    # in a run-scoped sidecar, not an unversioned new canonical field.
    assert "stage_metrics" not in record
    saved = json.loads((result.topic_dir / summary["diagnostic_path"]).read_text())
    assert saved["run_id"] == summary["run_id"]
    assert saved["stage"] == "extract"
    assert saved["diagnostic"] == detail
    assert events[-1].data["diagnostic"] == detail
    assert summary["pipeline_complete"] is False
    assert summary["output_dir"] is None
    assert "schema_invalid" in events[-1].message
    for path in result.topic_dir.rglob("*.json"):
        text = path.read_text()
        assert "response-secret-canary" not in text
        assert "credential-canary" not in text
