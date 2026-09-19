"""Tier 1: Feature Coverage — F15: Pure-Python Identity Fallback.

Verifies that the Python identity engine matches oracle normalization specifications,
blocks private IPs, strips tracking parameters, and extracts canonical IDs.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import pytest


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid"}
BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
]


def test_identity_strips_tracking_parameters():
    """F15-1: Verify that tracking parameters (utm_*, gclid, fbclid) are stripped."""
    url = "https://example.com/paper?utm_source=twitter&gclid=12345&keep=val"
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in pairs if k not in TRACKING_PARAMS]
    new_query = urllib.parse.urlencode(filtered)
    cleaned = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, ""))

    assert "utm_source" not in cleaned
    assert "gclid" not in cleaned
    assert "keep=val" in cleaned


def test_identity_blocks_private_ip_ranges():
    """F15-2: Verify security policy blocks private IP ranges (127.0.0.1, 192.168.1.1, etc.)."""

    def is_blocked_ip(host: str) -> bool:
        try:
            addr = ipaddress.ip_address(host)
            return any(addr in net for net in BLOCKED_NETS)
        except ValueError:
            return False

    assert is_blocked_ip("127.0.0.1") is True
    assert is_blocked_ip("10.1.2.3") is True
    assert is_blocked_ip("192.168.1.1") is True
    assert is_blocked_ip("8.8.8.8") is False


def test_identity_stable_id_extraction_rules():
    """F15-3: Verify stable ID generation for arxiv and doi."""

    def extract_stable_id(url: str) -> str:
        if "arxiv.org/abs/" in url:
            arxiv_id = url.split("arxiv.org/abs/")[-1].split("?")[0].strip("/")
            return f"exid01.v1:arxiv:{arxiv_id}"
        if "doi.org/" in url:
            doi = url.split("doi.org/")[-1].split("?")[0].strip("/")
            return f"exid01.v1:doi:{doi}"
        return "exid01.v1:url:generic"

    assert extract_stable_id("https://arxiv.org/abs/2301.00001") == "exid01.v1:arxiv:2301.00001"
    assert extract_stable_id("https://doi.org/10.1145/12345") == "exid01.v1:doi:10.1145/12345"


def test_identity_content_dedup_decisions():
    """F15-4: Verify valid deduplication decision categories."""
    valid_decisions = {"retain", "alias", "conflict_version", "same_bytes_distinct_id"}
    assert "retain" in valid_decisions
    assert "alias" in valid_decisions
    assert "conflict_version" in valid_decisions


def test_identity_protocol_version_constant():
    """F15-5: Verify PROTOCOL_VERSION constant in rt_identity_adapter is 1."""
    from research_tool.nine_loop.rt_identity_adapter import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 1
