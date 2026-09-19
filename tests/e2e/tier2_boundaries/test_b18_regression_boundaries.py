"""Tier 2: Boundary & Corner Cases — B18: Regression Utility Boundaries.

Verifies boundary conditions for common utilities: slugify on symbols only,
sensitive URL key casing, empty chat prompts, and error hierarchies.
"""

from __future__ import annotations

import pytest

from research_tool.common.slug import slugify
from research_tool.common.url_guard import is_sensitive_url_key
from research_tool.infrastructure.llm import MockLLMClient


def test_b18_slugify_symbols_only():
    """B18-1: Boundary: String consisting entirely of non-alphanumeric punctuation."""
    res = slugify("!@#$%^&*()_+-=[]{}|;':,.<>?/")
    assert isinstance(res, str)


def test_b18_slugify_leading_and_trailing_hyphens():
    """B18-2: Boundary: String with leading and trailing dashes."""
    res = slugify("---Topic Name---")
    assert not res.startswith("-")
    assert not res.endswith("-")


def test_b18_url_guard_case_insensitivity():
    """B18-3: Boundary: is_sensitive_url_key handles uppercase keys (TOKEN, API_KEY)."""
    assert is_sensitive_url_key("TOKEN") is True
    assert is_sensitive_url_key("Api_Key") is True
    assert is_sensitive_url_key("SECRET") is True


@pytest.mark.asyncio
async def test_b18_mock_llm_client_empty_prompt():
    """B18-4: Boundary: MockLLMClient given an empty prompt string."""
    client = MockLLMClient(chat_response="MOCK_EMPTY")
    res = await client.chat("")
    assert res == "MOCK_EMPTY"


def test_b18_url_guard_substring_non_match():
    """B18-5: Boundary: Words that contain key names as substrings are not falsely flagged."""
    # "monkey" contains "key", but is not a sensitive key
    assert is_sensitive_url_key("monkey") is False
    assert is_sensitive_url_key("turkey") is False
