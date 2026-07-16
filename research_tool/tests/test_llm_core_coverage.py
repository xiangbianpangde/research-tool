"""Behavioral coverage for the LLM core and its boundary helpers.

The SDKs are replaced at their network boundary; client request construction,
error translation, authentication latching, parsing, and streaming remain real.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from research_tool.common.url_guard import assert_safe_url
from research_tool.domain.errors import (
    LLMAuthenticationError,
    LLMError,
    UrlBlockedError,
    classify_llm_error,
)
from research_tool.domain.models import CollectorConfig, LLMConfig, OrganizerConfig
from research_tool.infrastructure.llm.anthropic_client import AnthropicLLMClient
from research_tool.infrastructure.llm.base import LLMClient, gather_fail_fast
from research_tool.infrastructure.llm.openai_client import (
    OpenAILLMClient,
    _parse_structured,
    _strip_think,
)


class Reply(BaseModel):
    value: int


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider detail for HTTP {status_code}")


class ResponseError(Exception):
    def __init__(self, status_code: int) -> None:
        self.response = SimpleNamespace(status_code=status_code)
        super().__init__("provider response detail")


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class AsyncChunks:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    def __aiter__(self):
        self._iterator = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeAnthropicMessages:
    def __init__(self, outcomes: list[object], streams: list[object] | None = None) -> None:
        self.outcomes = list(outcomes)
        self.streams = list(streams or [])
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.streams.pop(0)


class AnthropicStream:
    def __init__(self, values: list[str] | None = None, error: BaseException | None = None):
        self.text_stream = AsyncChunks(list(values or []))
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def openai_client(outcomes: list[object]) -> tuple[OpenAILLMClient, FakeCompletions]:
    client = object.__new__(OpenAILLMClient)
    LLMClient.__init__(client, LLMConfig(provider="openai", model="model", api_key="key"))
    completions = FakeCompletions(outcomes)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def anthropic_client(
    outcomes: list[object], streams: list[object] | None = None
) -> tuple[AnthropicLLMClient, FakeAnthropicMessages]:
    client = object.__new__(AnthropicLLMClient)
    LLMClient.__init__(
        client, LLMConfig(provider="anthropic", model="model", api_key="key")
    )
    messages = FakeAnthropicMessages(outcomes, streams)
    client._client = SimpleNamespace(messages=messages)
    return client, messages


def completion(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def chunk(content: str | None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def anthropic_response(*blocks: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type=block_type, text=text) for block_type, text in blocks]
    )


def test_error_classification_traverses_response_and_context_without_leaking_details():
    response_error = ResponseError(503)
    wrapped = RuntimeError("outer secret")
    wrapped.__context__ = response_error

    error = classify_llm_error("request", wrapped)

    assert type(error) is LLMError
    assert "HTTP 503" in str(error)
    assert "secret" not in str(error)
    assert error.__cause__ is wrapped


def test_error_classification_preserves_existing_authentication_error():
    original = LLMAuthenticationError("already safe")
    assert classify_llm_error("request", original) is original


def test_error_classification_handles_exception_without_http_status():
    provider_error = RuntimeError("sensitive provider detail")

    error = classify_llm_error("request", provider_error)

    assert str(error) == "LLM request 调用失败"
    assert error.__cause__ is provider_error


def test_url_guard_rejects_missing_hostname_and_ignores_unparseable_dns_entry(monkeypatch):
    with pytest.raises(UrlBlockedError, match="no hostname"):
        assert_safe_url("https:///paper")

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("not-an-ip", 0))],
    )
    assert_safe_url("https://papers.example")


def test_model_validators_cover_empty_proxy_and_invalid_node_bounds():
    assert CollectorConfig(proxy="  ").proxy is None
    with pytest.raises(ValueError, match="min_nodes.*不能大于 max_nodes"):
        OrganizerConfig(min_nodes=8, max_nodes=7)


@pytest.mark.asyncio
async def test_gather_fail_fast_accepts_empty_input():
    assert await gather_fail_fast([]) == []


def test_client_factory_dispatch_and_config_loaders(monkeypatch, tmp_path):
    import research_tool.domain.config as config_module
    import research_tool.infrastructure.llm.anthropic_client as anthropic_module
    import research_tool.infrastructure.llm.openai_client as openai_module

    monkeypatch.setattr(openai_module, "OpenAILLMClient", lambda config: ("openai", config))
    monkeypatch.setattr(
        anthropic_module, "AnthropicLLMClient", lambda config: ("anthropic", config)
    )

    openai_result = LLMClient.from_config(
        LLMConfig(provider="openai", model="m", api_key="key")
    )
    anthropic_result = LLMClient.from_config(
        LLMConfig(provider="anthropic", model="m", api_key="key")
    )
    assert openai_result[0] == "openai"
    assert anthropic_result[0] == "anthropic"

    invalid = LLMConfig.model_construct(provider="invalid", model="m")
    with pytest.raises(ValueError, match="不支持"):
        LLMClient.from_config(invalid)

    loaded = SimpleNamespace(llm=LLMConfig(provider="openai", model="loaded", api_key="key"))
    monkeypatch.setattr(config_module, "load_config", lambda path=None: loaded)
    assert LLMClient.from_yaml(tmp_path / "config.yaml")[1].model == "loaded"
    assert LLMClient.from_config_file()[1].model == "loaded"


def test_client_create_uses_defaults_and_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setattr(
        LLMClient,
        "from_config",
        classmethod(lambda cls, config: config),
    )

    config = LLMClient.create(provider="openai")
    custom = LLMClient.create(
        provider="ollama", api_key="explicit", model="custom", base_url="http://custom"
    )

    assert config.model == "gpt-4o-mini"
    assert config.api_key == "environment-key"
    assert custom.model == "custom"
    assert custom.base_url == "http://custom"


def test_openai_constructor_validates_key_and_wires_sdk(monkeypatch):
    import openai

    captured = {}
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: captured.update(kwargs) or "sdk")

    with pytest.raises(LLMError, match="缺少 api_key"):
        OpenAILLMClient(LLMConfig(provider="openai", model="m"))

    client = OpenAILLMClient(
        LLMConfig(provider="ollama", model="m", base_url="http://localhost:11434/v1")
    )
    assert client._client == "sdk"
    assert captured["api_key"] == "ollama"
    assert captured["max_retries"] == 0


def test_anthropic_constructor_validates_key_and_wires_sdk(monkeypatch):
    import anthropic

    captured = {}
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda **kwargs: captured.update(kwargs) or "sdk"
    )

    with pytest.raises(LLMError, match="缺少 api_key"):
        AnthropicLLMClient(LLMConfig(provider="anthropic", model="m"))

    client = AnthropicLLMClient(
        LLMConfig(provider="anthropic", model="m", api_key="key", base_url="https://llm")
    )
    assert client._client == "sdk"
    assert captured["api_key"] == "key"
    assert captured["base_url"] == "https://llm"
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_openai_chat_success_timeout_and_safe_provider_error():
    client, completions = openai_client(
        [completion("<think>private reasoning</think> answer"), asyncio.TimeoutError(), StatusError(502)]
    )

    assert await client.chat("prompt", system="system", temperature=0) == "answer"
    assert completions.calls[0]["messages"][0] == {"role": "system", "content": "system"}
    assert completions.calls[0]["temperature"] == 0
    with pytest.raises(LLMError, match="硬超时"):
        await client.chat("prompt")
    with pytest.raises(LLMError, match="HTTP 502") as caught:
        await client.chat("prompt")
    assert "provider detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_openai_chat_401_latches_authentication_failure():
    client, completions = openai_client([StatusError(401)])

    with pytest.raises(LLMAuthenticationError):
        await client.chat("prompt")
    with pytest.raises(LLMAuthenticationError, match="拒绝继续"):
        await client.chat("must not call sdk")
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_openai_structured_success_timeout_and_non_auth_error():
    client, completions = openai_client(
        [completion('{"value": 7}'), asyncio.TimeoutError(), StatusError(429)]
    )

    assert (await client.chat_structured("prompt", Reply, system="rules")).value == 7
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "rules" in completions.calls[0]["messages"][0]["content"]
    with pytest.raises(LLMError, match="chat_structured.*硬超时"):
        await client.chat_structured("prompt", Reply)
    with pytest.raises(LLMError, match="HTTP 429"):
        await client.chat_structured("prompt", Reply)


@pytest.mark.asyncio
async def test_openai_stream_yields_text_skips_empty_and_handles_failures():
    stream = AsyncChunks([chunk("a"), chunk(None), chunk("b")])
    client, _ = openai_client([stream, asyncio.TimeoutError(), StatusError(401)])

    assert [part async for part in client.stream("prompt", "system")] == ["a", "b"]
    with pytest.raises(LLMError, match="stream.*硬超时"):
        [part async for part in client.stream("prompt")]
    with pytest.raises(LLMAuthenticationError):
        [part async for part in client.stream("prompt")]
    with pytest.raises(LLMAuthenticationError, match="拒绝继续"):
        [part async for part in client.stream("must not call sdk")]


@pytest.mark.asyncio
async def test_openai_stream_suppresses_split_think_blocks():
    stream = AsyncChunks(
        [
            chunk("<thi"),
            chunk("nk>private reasoning"),
            chunk(" continues</thi"),
            chunk("nk>"),
            chunk("public answer"),
        ]
    )
    client, _ = openai_client([stream])

    assert [part async for part in client.stream("prompt")] == ["public answer"]


def test_openai_text_cleanup_and_structured_parser_paths():
    assert _strip_think("prefix</think> body") == "body"
    assert _strip_think("body <think>truncated") == "body"
    assert _parse_structured("```json\n{\"value\": 1}\n```", Reply).value == 1
    assert _parse_structured("explanation {\"value\": 2} after", Reply).value == 2

    with pytest.raises(LLMError, match="不是合法 JSON"):
        _parse_structured("explanation {broken} after", Reply)
    with pytest.raises(LLMError, match="不是合法 JSON"):
        _parse_structured("no object at all", Reply)
    with pytest.raises(LLMError, match="不符合预期 schema"):
        _parse_structured('{"value": "not an integer"}', Reply)


@pytest.mark.asyncio
async def test_anthropic_chat_success_timeout_and_safe_provider_error():
    client, messages = anthropic_client(
        [
            anthropic_response(("text", "a"), ("tool_use", "ignored"), ("text", "b")),
            asyncio.TimeoutError(),
            StatusError(502),
        ]
    )

    assert await client.chat("prompt", system="system", temperature=0) == "ab"
    assert messages.calls[0]["system"] == "system"
    assert messages.calls[0]["temperature"] == 0
    with pytest.raises(LLMError, match="chat.*硬超时"):
        await client.chat("prompt")
    with pytest.raises(LLMError, match="HTTP 502"):
        await client.chat("prompt")


@pytest.mark.asyncio
async def test_anthropic_structured_adds_schema_and_parses_response():
    client, messages = anthropic_client([anthropic_response(("text", '{"value": 9}'))])

    assert (await client.chat_structured("prompt", Reply, system="rules")).value == 9
    assert "rules" in messages.calls[0]["system"]
    assert "schema" in messages.calls[0]["system"]


@pytest.mark.asyncio
async def test_anthropic_stream_yields_text_and_handles_timeout_and_auth_failure():
    client, _ = anthropic_client(
        [],
        [
            AnthropicStream(["a", "b"]),
            AnthropicStream(error=asyncio.TimeoutError()),
            AnthropicStream(error=StatusError(401)),
        ],
    )

    assert [part async for part in client.stream("prompt", "system")] == ["a", "b"]
    with pytest.raises(LLMError, match="stream.*硬超时"):
        [part async for part in client.stream("prompt")]
    with pytest.raises(LLMAuthenticationError):
        [part async for part in client.stream("prompt")]
    with pytest.raises(LLMAuthenticationError, match="拒绝继续"):
        [part async for part in client.stream("must not call sdk")]
