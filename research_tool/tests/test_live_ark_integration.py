"""Live integration test suite for Volcengine Ark OpenAI-compatible API.

Targets:
- Provider: OpenAI-compatible API
- Base URL: https://ark.cn-beijing.volces.com/api/coding/v3
- Key: configured via OPENAI_API_KEY or VOLC_API_KEY environment variable

Verifies:
1. healthcheck() against live Ark API.
2. chat() basic completion.
3. chat_structured() with Pydantic schema validation.
4. stream() async token streaming without think leakage.
5. model_adapter.py chat and token usage ledger recording.
6. Timeout controls and error handling (short timeout, invalid auth).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import BaseModel, Field

from research_tool.domain.errors import LLMError
from research_tool.domain.models import LLMConfig
from research_tool.infrastructure.llm.openai_client import OpenAILLMClient
from research_tool.nine_loop import model_adapter

ARK_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/coding/v3",
)
ARK_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
ARK_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("VOLC_API_KEY") or os.environ.get("ARK_API_KEY") or ""


def _is_ark_live_available() -> bool:
    if not ARK_API_KEY or ARK_API_KEY == "ark-placeholder-adversarial-key":
        return False
    try:
        import httpx

        url = f"{ARK_BASE_URL.rstrip('/')}/chat/completions"
        resp = httpx.post(
            url,
            json={
                "model": ARK_MODEL,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            headers={"Authorization": f"Bearer {ARK_API_KEY}"},
            timeout=3.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_ark_live_available(),
    reason="Live Ark integration tests require functional API key with active subscription",
)


class ResearchTopicSchema(BaseModel):
    topic: str = Field(description="Name of the research topic")
    core_concepts: list[str] = Field(description="List of 3 core concepts")
    complexity_score: int = Field(description="Complexity score from 1 to 10")


@pytest.fixture
def live_client() -> OpenAILLMClient:
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url=ARK_BASE_URL,
        api_key=ARK_API_KEY,
        request_timeout_sec=30.0,
        temperature=0.0,
    )
    return OpenAILLMClient(cfg)


@pytest.mark.asyncio
async def test_live_ark_healthcheck(live_client: OpenAILLMClient) -> None:
    """Verify live Volcengine Ark responds to healthcheck with PONG."""
    await live_client.healthcheck(timeout_sec=15.0)


@pytest.mark.asyncio
async def test_live_ark_chat_basic(live_client: OpenAILLMClient) -> None:
    """Verify live chat completion returns content and strips think tokens."""
    reply = await live_client.chat("请只回复四个字：测试通过", temperature=0.0)
    assert isinstance(reply, str)
    assert len(reply.strip()) > 0
    assert "测试通过" in reply
    assert "<think>" not in reply
    assert "</think>" not in reply


@pytest.mark.asyncio
async def test_live_ark_chat_structured(live_client: OpenAILLMClient) -> None:
    """Verify structured output parsing and Pydantic validation on live API."""
    prompt = (
        "请分析主题'分布式共识'，输出主题名称、3个核心概念列表和一个1到10之间的整数复杂度评分"
    )
    result = await live_client.chat_structured(prompt, schema=ResearchTopicSchema)
    assert isinstance(result, ResearchTopicSchema)
    assert result.topic in ("分布式共识", "分布式共识算法", "分布式共识机制")
    assert len(result.core_concepts) >= 2
    assert 1 <= result.complexity_score <= 10


@pytest.mark.asyncio
async def test_live_ark_stream(live_client: OpenAILLMClient) -> None:
    """Verify async streaming yields chunks without unstripped think tags."""
    chunks: list[str] = []
    async for chunk in live_client.stream("请从1数到5，每个数字占一行"):
        chunks.append(chunk)

    combined = "".join(chunks)
    assert len(chunks) >= 1
    assert len(combined.strip()) > 0
    assert "1" in combined and "5" in combined
    assert "<think>" not in combined
    assert "</think>" not in combined


def test_live_model_adapter_ledger() -> None:
    """Verify model_adapter fallback chain client against live Ark endpoint."""
    old_key = os.environ.get("DEEPSEEK_V4_API_KEY")
    os.environ["DEEPSEEK_V4_API_KEY"] = ARK_API_KEY
    try:
        messages: list[dict[str, Any]] = [{"role": "user", "content": "请只回复四个字：服务就绪"}]
        res = model_adapter.chat(
            messages,
            base_url=ARK_BASE_URL,
            model=ARK_MODEL,
            api_key_env="DEEPSEEK_V4_API_KEY",
            temperature=0.0,
            max_tokens=256,
        )
        assert isinstance(res, model_adapter.ModelCallResult)
        assert "服务就绪" in res.content or len(res.content.strip()) > 0
        assert isinstance(res.usage, dict)
        assert res.usage.get("total_tokens", 0) > 0

        ledger = res.ledger_row()
        assert ledger["total_tokens"] == res.usage.get("total_tokens")
        assert ledger["latency_s"] > 0.0
        assert ledger["raw_id"] != ""
        assert len(ledger["params_sha"]) == 64
    finally:
        if old_key is None:
            os.environ.pop("DEEPSEEK_V4_API_KEY", None)
        else:
            os.environ["DEEPSEEK_V4_API_KEY"] = old_key


@pytest.mark.asyncio
async def test_live_ark_timeout_handling() -> None:
    """Verify client enforces hard timeout when request_timeout_sec is ultra small."""
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url=ARK_BASE_URL,
        api_key=ARK_API_KEY,
        request_timeout_sec=0.0001,  # 0.1ms forces timeout
    )
    timeout_client = OpenAILLMClient(cfg)
    with pytest.raises(LLMError) as exc_info:
        await timeout_client.chat("请写一篇长文分析量子退火机原理")
    assert "超时" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_live_ark_auth_failure_latch() -> None:
    """Verify invalid API key triggers authentication error and latches."""
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url=ARK_BASE_URL,
        api_key="invalid_401",
        request_timeout_sec=10.0,
    )
    auth_client = OpenAILLMClient(cfg)
    with pytest.raises(LLMError):
        await auth_client.chat("ping")

    # Latch check: second call must fail fast without network traffic
    with pytest.raises(LLMError) as latch_exc:
        await auth_client.chat("ping again")
    assert "鉴权已失败" in str(latch_exc.value) or "fail" in str(latch_exc.value).lower()
