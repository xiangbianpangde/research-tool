"""Tier 1: Feature Coverage — F18: Full Pytest Suite Regression.

Verifies regression stability across core utilities, domain errors,
slug generation, and mock LLM interfaces.
"""

from __future__ import annotations

import pytest

from research_tool.common.slug import slugify
from research_tool.common.url_guard import is_sensitive_url_key
from research_tool.domain.errors import (
    ResearchToolError,
    ConfigValidationError,
    StageError,
    LLMError,
    SearchError,
)
from research_tool.infrastructure.llm import MockLLMClient


def test_slugify_converts_chinese_and_english():
    """F18-1: Verify slugify handles both Chinese and English topic strings."""
    slug_en = slugify("Quantum Computing 2026")
    slug_cn = slugify("量子计算 调研")
    assert "quantum-computing-2026" in slug_en.lower()
    assert len(slug_cn) > 0


def test_url_guard_detects_sensitive_keys():
    """F18-2: Verify is_sensitive_url_key detects token, key, secret, password."""
    assert is_sensitive_url_key("api_key") is True
    assert is_sensitive_url_key("token") is True
    assert is_sensitive_url_key("secret") is True
    assert is_sensitive_url_key("page") is False
    assert is_sensitive_url_key("query") is False


def test_domain_error_hierarchy_inherits_research_tool_error():
    """F18-3: Verify domain error classes inherit from ResearchToolError."""
    assert issubclass(ConfigValidationError, ResearchToolError)
    assert issubclass(StageError, ResearchToolError)
    assert issubclass(LLMError, ResearchToolError)
    assert issubclass(SearchError, ResearchToolError)


@pytest.mark.asyncio
async def test_mock_llm_client_returns_content():
    """F18-4: Verify MockLLMClient generates responses without external network."""
    client = MockLLMClient(chat_response="Simulated LLM response")
    res = await client.chat("Hello")
    assert res == "Simulated LLM response"


def test_research_tool_error_formatting():
    """F18-5: Verify ResearchToolError string formatting and message preservation."""
    err = ResearchToolError("Failed stage execution")
    assert str(err) == "Failed stage execution"
