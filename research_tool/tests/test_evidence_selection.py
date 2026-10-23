"""Bind model choices to immutable source spans instead of model-copied quotations."""

from __future__ import annotations

import hashlib
import json

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.errors import StageError, find_llm_diagnostic
from research_tool.domain.models import ExtractorConfig, PipelineConfig
from research_tool.infrastructure.stages.extractor import (
    Extractor, _build_prompt, _verified_claims, _is_structured_failure,
)
from research_tool.nine_loop.evidence import parse_source, verify_claim
from research_tool.tests.test_llm_portability import openai_client
from research_tool.tests.test_structured_extraction_diagnostics import compatible, response


def units(chunk):
    prompt = _build_prompt(ExtractorConfig(), chunk)
    return json.loads(prompt.split("EVIDENCE_UNITS:\n", 1)[1])


def source(body):
    return parse_source("---\nsource: https://papers.invalid/one\ntitle: Study\n---\n\n" + body)


def test_near_maximum_chunk_text_is_sent_once_with_bounded_unit_overhead():
    sentences = [f"UniqueEvidence{i:04d} reports measured clinical outcomes and uncertainty." for i in range(700)]
    chunk = " ".join(sentences)[:50000]
    prompt = _build_prompt(ExtractorConfig(chunk_size=50000), chunk)
    assert all(prompt.count(sentence) == 1 for sentence in sentences if sentence in chunk)
    assert len(prompt) < 2.5 * len(chunk)


def test_prompt_has_scoped_choices_not_a_request_to_copy_quotes():
    chunk = "There were 19/89 deliveries with bleeding. The OR was 5.8 (95% CI, 1.2–28.0)."
    choices = units(chunk)
    assert len(choices) >= 2
    assert all(choice["text"] == chunk[choice["start"]:choice["end"]] for choice in choices)
    assert len({choice["evidence_id"] for choice in choices}) == len(choices)
    other = units("Different source content.")
    assert not {c["evidence_id"] for c in choices} & {c["evidence_id"] for c in other}


@pytest.mark.parametrize("body", [
    "Bleeding occurred in 38.8% (19/49) of female patients.\nThe ﬁbrinogen concentration was 0.64–0.65 g/L.",
    "表型与功能分开。\n\n流产不是出血；未报告不是阴性。",
    "Left column result.                Right column limitation.\ncontinued left text.          continued right text.",
])
def test_selected_original_span_preserves_unicode_whitespace_and_denominators(body):
    src = source(body)
    for choice in units(body):
        claims = _verified_claims(src, body, [{"evidence_id": choice["evidence_id"], "topic": "results"}])
        claim = claims[0]
        span = claim["citations"][0]
        assert claim["text"] == body[span["start_offset"]:span["end_offset"]]
        assert claim["text"] == choice["text"]
        assert span["content_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        assert verify_claim(claim, {src["source_id"]: body}).valid


def test_model_rewrite_never_enters_ledger_when_a_valid_choice_is_present():
    body = "Nineteen of 89 deliveries were complicated by bleeding."
    selected = units(body)[0]
    claim = _verified_claims(source(body), body, [{
        "evidence_id": selected["evidence_id"],
        "text": "All patients will bleed.", "quote": "invented-quotation-canary", "topic": "results",
    }])[0]
    assert claim["text"] == body
    assert "invented-quotation-canary" not in json.dumps(claim)
    assert "All patients will bleed" not in json.dumps(claim)


@pytest.mark.parametrize("kind", ["unknown", "sibling", "empty"])
def test_unrecognized_choice_fails_closed_without_echoing_model_identifier(kind):
    body = "This source reports the first result."
    choice = {"unknown": "private-model-canary", "sibling": units("Sibling evidence.")[0]["evidence_id"], "empty": ""}[kind]
    with pytest.raises(StageError) as caught:
        _verified_claims(source(body), body, [{"evidence_id": choice, "quote": body}])
    detail = find_llm_diagnostic(caught.value)
    assert detail is not None
    assert detail.reason == "selection_invalid"
    assert not _is_structured_failure(caught.value)
    assert "private-model-canary" not in str(caught.value) + detail.model_dump_json()


def test_legacy_nonverbatim_quote_keeps_a_safe_diagnostic_instead_of_disappearing():
    body = "A measured result was reported."
    with pytest.raises(StageError) as caught:
        _verified_claims(source(body), body, [{"quote": "a private-quotation-canary was invented"}])
    detail = find_llm_diagnostic(caught.value)
    assert detail is not None and detail.reason == "quote_mismatch"
    assert "private-quotation-canary" not in str(caught.value) + detail.model_dump_json()


def test_selection_offsets_identify_this_chunk_occurrence_not_the_first_in_source():
    sentence = "The study reports an observed result."
    prefix = sentence + " Padding. "
    src = source(prefix + sentence)
    choice = units(sentence)[0]
    claim = _verified_claims(src, sentence, [{"evidence_id": choice["evidence_id"]}], chunk_offset=len(prefix))[0]
    span = claim["citations"][0]
    assert span["start_offset"] == len(prefix)
    assert span["end_offset"] == len(prefix) + len(sentence)


@pytest.mark.asyncio
async def test_invalid_selection_persists_source_chunk_and_index_but_never_partial_claims(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    body = "A clinically relevant result was observed in the original study."
    (inputs / "paper.md").write_text("<!-- source: https://papers.invalid/one -->\n\n" + body)
    good = units(body)[0]["evidence_id"]
    answer = json.dumps({"claims": [{"evidence_id": good}, {"evidence_id": "private-model-canary"}]})
    client, calls = openai_client([response("PONG"), response(answer)])
    compatible(client)
    config = PipelineConfig(topic="risk", work_dir=tmp_path / "output", input_dir=inputs,
                            llm=client.config, llm_stage_attempts=3, llm_retry_backoff_sec=0)
    config.cleaner.min_content_length = 20
    config.collector.strategies.enabled = False

    result = await ResearchPipeline(config, llm=client).run()

    assert result.verdict == "STOP_ERROR" and result.failed_stage == "extract"
    summary = json.loads(result.run_summary_path.read_text())
    detail = summary["stage_metrics"][-1]["diagnostic"]
    assert detail["reason"] == "selection_invalid"
    assert detail["source_id"] == source(body)["source_id"]
    assert detail["chunk_index"] == 0 and detail["selection_index"] == 1
    assert detail["chunk_chars"] == len(body)
    assert len(calls.calls) == 2  # semantic evidence failures are not blindly retried
    assert not list(result.topic_dir.glob("runs/*/initial/extracted/claims.json"))
    assert not list(result.topic_dir.glob("runs/*/initial/extracted/.chunk-cache.json"))
    for path in result.topic_dir.rglob("*.json"):
        assert "private-model-canary" not in path.read_text()


@pytest.mark.asyncio
async def test_selected_claim_cache_is_revalidated_and_reused_without_another_model_call(tmp_path):
    inputs = tmp_path / "clean"
    inputs.mkdir()
    body = "A measured result was reported in 89 deliveries."
    (inputs / "paper.md").write_text("<!-- source: https://papers.invalid/one -->\n\n" + body)
    selected = units(body)[0]["evidence_id"]
    client, calls = openai_client([response(json.dumps({"claims": [{"evidence_id": selected}]}))])
    compatible(client)
    extractor = Extractor(ExtractorConfig(fail_on_chunk_error=True))
    result = await extractor.run(inputs, client)
    again = await extractor.run(inputs, client)
    assert result.claims == again.claims and len(calls.calls) == 1
    cache = json.loads((tmp_path / "extracted/.chunk-cache.json").read_text())
    assert cache["entries"][0]["result"]["claims"][0]["evidence_id"] == selected
    assert verify_claim(result.claims[0], {source(body)["source_id"]: body}).valid
