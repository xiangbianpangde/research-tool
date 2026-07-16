from __future__ import annotations

import asyncio
import logging

import pytest

from research_tool.domain.errors import LLMAuthenticationError, LLMError
from research_tool.domain.models import LLMConfig
from research_tool.infrastructure.llm.anthropic_client import AnthropicLLMClient
from research_tool.infrastructure.llm.base import LLMClient
from research_tool.infrastructure.llm.openai_client import OpenAILLMClient
from research_tool.common.logging_config import RuntimeSecretFilter


class _StatusError(Exception):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("SECRET_MARKER provider response body")


class _AnthropicMessages:
    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        raise _StatusError()


@pytest.mark.asyncio
async def test_anthropic_401_sets_latch_and_redacts_provider_error():
    client = object.__new__(AnthropicLLMClient)
    LLMClient.__init__(
        client,
        LLMConfig(
            provider="anthropic",
            model="MiniMax-M3",
            api_key="".join(("fixture", "-", "credential")),
        ),
    )
    messages = _AnthropicMessages()
    client._client = type("SDK", (), {"messages": messages})()

    with pytest.raises(LLMAuthenticationError) as first:
        await client.chat("hello")
    with pytest.raises(LLMAuthenticationError):
        await client.chat("must not reach sdk")

    assert messages.calls == 1
    assert "SECRET_MARKER" not in str(first.value)


class _Completions:
    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        raise _StatusError()


@pytest.mark.asyncio
async def test_openai_structured_401_sets_latch_and_redacts_provider_error():
    client = object.__new__(OpenAILLMClient)
    LLMClient.__init__(
        client,
        LLMConfig(
            provider="openai",
            model="test",
            api_key="".join(("fixture", "-", "credential")),
        ),
    )
    completions = _Completions()
    client._client = type(
        "SDK",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()

    from pydantic import BaseModel

    class Reply(BaseModel):
        ok: bool = False

    with pytest.raises(LLMAuthenticationError) as first:
        await client.chat_structured("hello", Reply)
    with pytest.raises(LLMAuthenticationError):
        await client.chat("must not reach sdk")

    assert completions.calls == 1
    assert "SECRET_MARKER" not in str(first.value)


class _ProbeClient(LLMClient):
    def __init__(self, reply: str | None) -> None:
        super().__init__(LLMConfig(provider="openai", api_key="test"))
        self.reply = reply

    async def chat(self, prompt, system=None, temperature=None):
        if self.reply is None:
            await asyncio.sleep(60)
        return self.reply or ""

    async def chat_structured(self, prompt, schema, system=None):
        return schema()

    async def stream(self, prompt, system=None):
        if False:
            yield ""


@pytest.mark.asyncio
async def test_healthcheck_rejects_non_pong():
    with pytest.raises(LLMError, match="未返回 PONG"):
        await _ProbeClient("HTML error page").healthcheck(timeout_sec=0.1)


@pytest.mark.asyncio
async def test_healthcheck_has_short_independent_timeout():
    with pytest.raises(LLMError, match="健康检查超时"):
        await _ProbeClient(None).healthcheck(timeout_sec=0.01)


def test_runtime_log_filter_redacts_environment_secret(monkeypatch):
    secret = "sk-" + "test-SECRET_MARKER-123456789"
    monkeypatch.setenv("MINIMAX_API_KEY", secret)
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed %s", (secret,), None)

    assert RuntimeSecretFilter().filter(record) is True
    assert "SECRET_MARKER" not in record.getMessage()
