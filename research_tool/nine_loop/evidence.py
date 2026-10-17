"""Pure helpers for parsing source bodies and checking evidence spans."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping


_METADATA_RE = re.compile(
    r"^[ \t]*<!--[ \t]*(?P<key>[A-Za-z_]+)[ \t]*:[ \t]*"
    r"(?P<value>.*?)[ \t]*-->[ \t]*$",
)


@dataclass(frozen=True)
class EvidenceSpan:
    """A quote whose offsets are relative to one source's body text."""

    source_id: str
    locator: str
    content_sha256: str
    start_offset: int
    end_offset: int
    text: str


@dataclass(frozen=True, order=True)
class ClaimVerification:
    """Small sortable result returned by verify_claim."""

    claim_id: str
    valid: bool
    verified_span_count: int = 0


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_metadata(markdown: str) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    position = 0
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end >= 0:
            import yaml
            try:
                loaded = yaml.safe_load(markdown[3:end]) or {}
            except yaml.YAMLError as exc:
                raise ValueError("invalid YAML front matter") from exc
            if isinstance(loaded, dict):
                metadata.update({str(key): str(value) for key, value in loaded.items()})
            return metadata, markdown[end + len("\n---"):].lstrip("\r\n")
    while position < len(markdown):
        line_end = markdown.find("\n", position)
        if line_end == -1:
            line_end = len(markdown)
        line = markdown[position:line_end].rstrip("\r")
        match = _METADATA_RE.match(line)
        if match is None:
            break
        metadata[match.group("key")] = match.group("value")
        position = line_end + (line_end < len(markdown))

    body = markdown[position:]
    if not metadata and body == markdown:
        return metadata, body
    return metadata, body.lstrip("\r\n")


def parse_source(markdown: str) -> dict[str, str]:
    """Parse leading raw Markdown metadata and return its source record.

    The returned text is the body after leading metadata comments. Its hash
    and all evidence offsets therefore refer to the actual body text.
    """
    if not isinstance(markdown, str):
        raise TypeError("markdown must be str")

    metadata, text = _parse_metadata(markdown)
    locator = metadata.get("source", metadata.get("locator", ""))
    locator = locator or metadata.get("video_url", "")
    source_id_basis = locator or text
    record = {
        "source_id": _sha256(source_id_basis)[:16],
        "locator": locator,
        "title": metadata.get("title", metadata.get("video_title", "")),
        "text": text,
        "content_sha256": _sha256(text),
    }
    if "evidence_kind" in metadata:
        record["evidence_kind"] = metadata["evidence_kind"]
    return record


def _as_span(value: EvidenceSpan | Mapping[str, Any]) -> EvidenceSpan | None:
    if isinstance(value, EvidenceSpan):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return EvidenceSpan(
            source_id=value["source_id"],
            locator=value["locator"],
            content_sha256=value["content_sha256"],
            start_offset=value["start_offset"],
            end_offset=value["end_offset"],
            text=value["text"],
        )
    except (KeyError, TypeError):
        return None


def verify_span(source_text: str, span: EvidenceSpan) -> bool:
    """Return whether span exactly identifies text in source_text."""
    candidate = _as_span(span)
    if candidate is None or not isinstance(source_text, str):
        return False
    if not all(
        isinstance(offset, int) and not isinstance(offset, bool)
        for offset in (candidate.start_offset, candidate.end_offset)
    ):
        return False
    if not 0 <= candidate.start_offset < candidate.end_offset <= len(source_text):
        return False
    if not candidate.text.strip() or not candidate.source_id or not candidate.locator:
        return False
    if candidate.text != source_text[candidate.start_offset:candidate.end_offset]:
        return False
    return candidate.content_sha256 == _sha256(source_text)


def verify_claim(
    claim: Mapping[str, Any], source_texts: Mapping[str, str]
) -> ClaimVerification:
    """Verify a claim when at least one citation resolves to a valid span."""
    claim_id = str(claim.get("claim_id", "")) if isinstance(claim, Mapping) else ""
    citations = claim.get("citations", []) if isinstance(claim, Mapping) else []
    if not isinstance(citations, list) or not isinstance(source_texts, Mapping):
        return ClaimVerification(claim_id=claim_id, valid=False)

    verified = 0
    for raw_span in citations:
        span = _as_span(raw_span)
        if span is None or span.source_id not in source_texts:
            continue
        if verify_span(source_texts[span.source_id], span):
            verified += 1
    return ClaimVerification(
        claim_id=claim_id,
        valid=verified > 0,
        verified_span_count=verified,
    )


__all__ = [
    "ClaimVerification",
    "EvidenceSpan",
    "parse_source",
    "verify_claim",
    "verify_span",
]
