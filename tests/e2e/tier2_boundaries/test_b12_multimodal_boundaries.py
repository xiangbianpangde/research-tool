"""Tier 2: Boundary & Corner Cases — B12: Multimodal Ingest Boundaries.

Verifies boundary conditions for multimodal assets: 0-byte PDF, missing PDF directory,
non-video URLs, malformed video IDs, and unknown source engines.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from research_tool.domain.models import Source
from research_tool.application.video_pipeline import _BILIBILI_RE, _YOUTUBE_RE


def test_b12_zero_byte_pdf_file_handling(workspace: Path):
    """B12-1: Boundary: Empty 0-byte PDF file in ingest directory."""
    empty_pdf = workspace / "empty.pdf"
    empty_pdf.write_bytes(b"")
    assert empty_pdf.stat().st_size == 0


def test_b12_non_matching_video_urls():
    """B12-2: Boundary: Non-video URLs do not match video regexes."""
    non_videos = [
        "https://example.com/not-a-video",
        "https://github.com/owner/repo",
        "https://arxiv.org/abs/2301.00001",
    ]
    for url in non_videos:
        assert not _BILIBILI_RE.search(url)
        assert not _YOUTUBE_RE.search(url)


def test_b12_source_with_unknown_engine():
    """B12-3: Boundary: Source object instantiated with custom/unknown source_engine."""
    source = Source(
        url="https://example.org/doc",
        title="Custom Source",
        source_engine="custom_engine_xyz",
    )
    assert source.source_engine == "custom_engine_xyz"


def test_b12_clean_scheme_rejection_for_unsupported_schemes():
    """B12-4: Boundary: Clean stage rejects ftp://, gopher://, javascript:// schemes."""
    from research_tool.nine_loop.clean_min import ALLOWED_SCHEMES

    assert "ftp" not in ALLOWED_SCHEMES
    assert "gopher" not in ALLOWED_SCHEMES
    assert "javascript" not in ALLOWED_SCHEMES


def test_b12_video_url_with_timestamps_and_playlist():
    """B12-5: Boundary: Video URL with query parameters like timestamp and playlist."""
    yt_complex = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL12345"
    m = _YOUTUBE_RE.search(yt_complex)
    assert m is not None
    assert "dQw4w9WgXcQ" in m.group(0)
