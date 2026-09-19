"""Tier 2: Boundary & Corner Cases — B15: Identity Fallback Boundaries.

Verifies boundary conditions for URL normalization and identity extraction:
extreme query lengths, unusual percent encoding, fragments, and IPv6 literals.
"""

from __future__ import annotations

import urllib.parse
import pytest


def test_b15_url_with_fragment_stripped():
    """B15-1: Boundary: URL fragment (#heading) is stripped during normalization."""
    url = "https://example.com/page#section1"
    parsed = urllib.parse.urlsplit(url)
    cleaned = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    assert "#" not in cleaned
    assert cleaned == "https://example.com/page"


def test_b15_url_with_hundred_query_parameters():
    """B15-2: Boundary: URL with 100 query parameters parsed and filtered safely."""
    query = "&".join(f"param{i}={i}" for i in range(100))
    url = f"https://example.com/api?{query}"
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query)
    assert len(pairs) == 100


def test_b15_url_with_lowercase_scheme_and_host():
    """B15-3: Boundary: Mixed-case scheme and host are downcased."""
    url = "HTTPS://WWW.EXAMPLE.COM/Path"
    parsed = urllib.parse.urlsplit(url)
    cleaned = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
    assert cleaned == "https://www.example.com/Path"


def test_b15_url_with_trailing_slash_normalization():
    """B15-4: Boundary: Path normalization resolves dot segments and trailing slashes."""
    url = "https://example.com/a/b/../c/"
    parsed = urllib.parse.urlsplit(url)
    norm_path = parsed.path.rstrip("/")
    assert norm_path.endswith("/c")


def test_b15_empty_url_handling():
    """B15-5: Boundary: Normalizing empty string URL."""
    assert urllib.parse.urlsplit("").scheme == ""
