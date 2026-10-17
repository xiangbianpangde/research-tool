from __future__ import annotations

import hashlib

import pytest

from research_tool.nine_loop.evidence import (
    EvidenceSpan,
    parse_source,
    verify_claim,
    verify_span,
)


def test_parse_source_extracts_metadata_and_hashes_only_body() -> None:
    body = "正文第一行\n第二行。"
    markdown = (
        "<!-- title: 示例来源 -->\n"
        "<!-- source: https://example.test/article -->\n\n"
        + body
    )

    source = parse_source(markdown)

    assert source["locator"] == "https://example.test/article"
    assert source["title"] == "示例来源"
    assert source["text"] == body
    assert source["content_sha256"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert source["source_id"]


def test_video_frontmatter_keeps_original_locator_and_body():
    markdown = (
        "---\nvideo_title: Experiment\nvideo_url: https://youtube.com/watch?v=abc\n---\n"
        "The transcript reports a measured value.\n"
    )
    source = parse_source(markdown)
    assert source["locator"] == "https://youtube.com/watch?v=abc"
    assert source["title"] == "Experiment"
    assert source["text"] == "The transcript reports a measured value.\n"


def test_verify_span_accepts_exact_body_offsets_and_hash() -> None:
    text = "alpha claim omega"
    start = text.index("claim")
    span = EvidenceSpan(
        source_id="src-1",
        locator="https://example.test",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        start_offset=start,
        end_offset=start + len("claim"),
        text="claim",
    )

    assert verify_span(text, span) is True


def test_verify_span_rejects_misaligned_text_even_if_locator_is_long() -> None:
    text = "short body"
    span = EvidenceSpan(
        source_id="src-1",
        locator="https://example.test/this/is/a/long/locator",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        start_offset=0,
        end_offset=5,
        text="wrong",
    )

    assert verify_span(text, span) is False


def test_verify_span_rejects_source_body_hash_mismatch() -> None:
    text = "the actual source body"
    span = EvidenceSpan(
        source_id="src-1",
        locator="https://example.test",
        content_sha256="0" * 64,
        start_offset=0,
        end_offset=3,
        text="the",
    )

    assert verify_span(text, span) is False


def test_verify_claim_requires_at_least_one_resolvable_span() -> None:
    claim = {"claim_id": "claim-1", "text": "fact", "citations": []}

    result = verify_claim(claim, {"src-1": "fact"})

    assert result.valid is False
    assert result.claim_id == "claim-1"


def test_verify_claim_reports_valid_claim_and_is_sortable() -> None:
    source_text = "claim appears here"
    start = source_text.index("claim")
    span = {
        "source_id": "src-1",
        "locator": "https://example.test",
        "content_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "start_offset": start,
        "end_offset": start + len("claim"),
        "text": "claim",
    }
    claim = {"claim_id": "claim-2", "text": "fact", "citations": [span]}

    result = verify_claim(claim, {"src-1": source_text})

    assert result.valid is True
    assert sorted([result, result])[0] == result


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 2), (0, 100), (3, 2)],
)
def test_verify_span_rejects_out_of_bounds_offsets(start: int, end: int) -> None:
    text = "body"
    span = EvidenceSpan(
        source_id="src-1",
        locator="https://example.test",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        start_offset=start,
        end_offset=end,
        text="",
    )

    assert verify_span(text, span) is False
