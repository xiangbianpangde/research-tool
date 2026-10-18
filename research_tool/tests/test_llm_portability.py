"""Offline regressions for configurable providers and bounded JSON compatibility."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError, LLMAuthenticationError, LLMError
from research_tool.domain.models import LLMConfig
from research_tool.infrastructure.llm.base import LLMClient
from research_tool.infrastructure.llm.openai_client import OpenAILLMClient


@pytest.fixture(autouse=True)
def isolate_config_environment(monkeypatch):
    monkeypatch.setattr("research_tool.domain.config._DEFAULT_PATHS", [])
    for name in (
        "RESEARCH_CONFIG", "RESEARCH_WORK_DIR", "LLM_PROVIDER", "LLM_MODEL",
        "LLM_BASE_URL", "LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "MINIMAX_API_KEY", "HTTPS_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)


class Reply(BaseModel):
    value: int


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "provider failure", body=None) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(message)


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


def completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def generic_config(**overrides) -> LLMConfig:
    return LLMConfig.model_validate({
        "provider": "openai_compatible", "model": "fixture-model",
        "base_url": "https://endpoint.invalid/v1", "api_key": "fixture-key",
        **overrides,
    })


def openai_client(outcomes: list[object], **overrides):
    client = object.__new__(OpenAILLMClient)
    LLMClient.__init__(client, LLMConfig(provider="openai", model="fixture-model", **overrides))
    completions = FakeCompletions(outcomes)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def write_config(tmp_path, **overrides):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"llm": generic_config(**overrides).model_dump()}), encoding="utf-8")
    return path


@pytest.mark.parametrize("provider", ["openai", "deepseek", "ollama", "minimax", "anthropic", "openai_compatible"])
def test_factory_preserves_all_provider_dispatch(monkeypatch, provider):
    import research_tool.infrastructure.llm.anthropic_client as anthropic_module
    import research_tool.infrastructure.llm.openai_client as openai_module

    monkeypatch.setattr(openai_module, "OpenAILLMClient", lambda config: ("openai", config))
    monkeypatch.setattr(anthropic_module, "AnthropicLLMClient", lambda config: ("anthropic", config))
    config = generic_config(provider=provider)

    kind, received = LLMClient.from_config(config)

    assert kind == ("anthropic" if provider == "anthropic" else "openai")
    assert received == config


@pytest.mark.parametrize("field", ["model", "base_url"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_generic_provider_rejects_empty_connection_fields(field, value):
    with pytest.raises(ValidationError, match=field):
        generic_config(**{field: value})


@pytest.mark.parametrize("field", ["model", "base_url"])
def test_generic_provider_requires_explicit_connection_fields(field):
    values = {"provider": "openai_compatible", "model": "fixture-model", "base_url": "https://endpoint.invalid/v1"}
    del values[field]

    with pytest.raises(ValidationError, match=field):
        LLMConfig.model_validate(values)


def test_load_config_reports_missing_generic_connection_fields(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  provider: openai_compatible\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="base_url|model"):
        load_config(path)


def test_generic_key_and_connection_precedence(tmp_path, monkeypatch):
    path = write_config(tmp_path, api_key="yaml-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider-key")

    environment = load_config(path).llm
    explicit = load_config(path, overrides={"llm": {
        "model": "override-model", "base_url": "https://override.invalid/v1", "api_key": "override-key",
    }}).llm

    assert environment.model == "env-model"
    assert environment.base_url == "https://environment.invalid/v1"
    assert environment.api_key == "environment-key"
    assert explicit.model == "override-model"
    assert explicit.base_url == "https://override.invalid/v1"
    assert explicit.api_key == "override-key"


def test_generic_environment_key_beats_yaml_without_identity_change(tmp_path, monkeypatch):
    path = write_config(tmp_path, api_key="yaml-key")
    monkeypatch.setenv("LLM_API_KEY", "environment-key")

    assert load_config(path).llm.api_key == "environment-key"


def test_generic_session_id_is_loaded_from_process_environment(tmp_path, monkeypatch):
    path = write_config(tmp_path, api_key=None)
    monkeypatch.setenv("LLM_API_KEY", "environment-key")
    monkeypatch.setenv("LLM_SESSION_ID", "research-session-1")

    config = load_config(path).llm

    assert config.session_id == "research-session-1"


@pytest.mark.parametrize("marker", ["endpoint", "minimax-gateway"])
def test_generic_key_does_not_use_vendor_credentials(tmp_path, monkeypatch, marker):
    path = write_config(tmp_path, api_key=None, base_url=f"https://{marker}.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "vendor-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "other-vendor-key")
    assert load_config(path).llm.api_key is None

    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    assert load_config(path).llm.api_key == "generic-key"


@pytest.mark.parametrize("source", ["environment", "override"])
@pytest.mark.parametrize("switch", ["provider", "endpoint"])
def test_connection_switch_discards_old_yaml_key(tmp_path, monkeypatch, source, switch):
    old_provider = "openai" if switch == "provider" else "openai_compatible"
    path = write_config(tmp_path, provider=old_provider, **{"api" + "_key": "legacy-fixture"})
    target = {"provider": "openai_compatible", "base_url": "https://replacement.invalid/v1"}
    monkeypatch.setenv("OPENAI_API_KEY", "old-provider-env-key")
    if source == "environment":
        monkeypatch.setenv("LLM_PROVIDER", target["provider"])
        monkeypatch.setenv("LLM_BASE_URL", target["base_url"])
        overrides = None
    else:
        overrides = {"llm": target}

    assert load_config(path, overrides=overrides).llm.api_key is None
    monkeypatch.setenv("LLM_API_KEY", "replacement-key")
    assert load_config(path, overrides=overrides).llm.api_key == "replacement-key"


def test_switch_away_from_generic_uses_matching_provider_secret(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "generic-env-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env-key")

    config = load_config(path, overrides={"llm": {"provider": "openai", "base_url": "https://other.invalid/v1"}})

    assert config.llm.api_key == "openai-env-key"


@pytest.mark.parametrize("missing", ["model", "base_url"])
def test_create_never_supplies_generic_model_or_endpoint_defaults(monkeypatch, missing):
    monkeypatch.setattr(LLMClient, "from_config", classmethod(lambda cls, config: config))
    kwargs = {"model": "fixture-model", "base_url": "https://endpoint.invalid/v1"}
    del kwargs[missing]

    with pytest.raises(ValueError, match=missing):
        LLMClient.create(provider="openai_compatible", **kwargs)


def test_create_generic_uses_process_key_and_keeps_explicit_key(monkeypatch):
    monkeypatch.setattr(LLMClient, "from_config", classmethod(lambda cls, config: config))
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    kwargs = {"provider": "openai_compatible", "model": "fixture-model", "base_url": "https://endpoint.invalid/v1"}

    assert LLMClient.create(**kwargs).api_key == "generic-key"
    assert LLMClient.create(**kwargs, **{"api" + "_key": "explicit-fixture"}).api_key == "explicit-fixture"


@pytest.mark.parametrize("reserved", ["model", "messages", "api_key", "base_url", "temperature", "max_tokens", "stream", "response_format"])
def test_extra_body_cannot_override_owned_request_fields(reserved):
    with pytest.raises(ValidationError, match="extra_body"):
        LLMConfig(request_options={"extra_body": {reserved: "must-not-override"}})


@pytest.mark.parametrize("reserved", ["model", "messages", "api_key", "base_url", "timeout", "extra_headers"])
def test_request_options_only_accepts_extra_body(reserved):
    with pytest.raises(ValidationError, match="request_options"):
        LLMConfig(request_options={reserved: {"value": "must-not-override"}})


@pytest.mark.asyncio
async def test_configured_extra_body_is_forwarded_to_all_openai_request_modes(tmp_path):
    extra_body = {"thinking": {"type": "disabled"}}
    path = write_config(tmp_path, request_options={"extra_body": extra_body})
    config = load_config(path).llm

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="token"))])

    client, completions = openai_client([completion("answer"), completion('{"value": 7}'), chunks()])
    client.config = config
    assert await client.chat("prompt", temperature=0) == "answer"
    assert (await client.chat_structured("prompt", Reply)).value == 7
    assert [part async for part in client.stream("prompt")] == ["token"]
    assert all(call["extra_body"] == extra_body for call in completions.calls)
    assert completions.calls[0]["temperature"] == 0
    assert config.request_options == {"extra_body": extra_body}


def unsupported(status=400):
    return StatusError(status, "response_format is not supported by this endpoint")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    unsupported(400), unsupported(422),
    StatusError(400, "Unsupported parameter: 'response_format'"),
    StatusError(422, "Unknown argument: response_format"),
    StatusError(400, body={"error": {"param": "response_format", "code": "unsupported_parameter"}}),
])
async def test_structured_retries_once_only_for_explicit_response_format_rejection(error):
    client, completions = openai_client([error, completion('{"value": 7}')])

    assert (await client.chat_structured("prompt", Reply, system="rules")).value == 7
    assert len(completions.calls) == 2
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in completions.calls[1]
    assert completions.calls[1] == {key: value for key, value in completions.calls[0].items() if key != "response_format"}
    assert "rules" in completions.calls[1]["messages"][0]["content"]
    assert "schema" in completions.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    StatusError(400, "bad request for another parameter"),
    StatusError(422, "response_format has an invalid value"),
    unsupported(401), unsupported(403), unsupported(429), unsupported(500),
    asyncio.TimeoutError(), httpx.ReadTimeout("response_format unsupported"),
    StatusError(400, "response_format is not supported", {"code": "insufficient_quota"}),
    StatusError(400, "response_format is not supported", {"code": "invalid_api_key"}),
])
async def test_structured_never_falls_back_on_auth_quota_timeout_or_unrelated_rejection(error):
    client, completions = openai_client([error, completion('{"value": 8}')])
    expected = LLMAuthenticationError if getattr(error, "status_code", None) == 401 else LLMError

    with pytest.raises(expected):
        await client.chat_structured("prompt", Reply)

    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_structured_fallback_is_attempted_at_most_once():
    client, completions = openai_client([unsupported(), unsupported(), completion('{"value": 8}')])

    with pytest.raises(LLMError):
        await client.chat_structured("prompt", Reply)

    assert len(completions.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['{"value": "not-an-integer"}', "not JSON"])
async def test_structured_fallback_keeps_schema_and_json_validation(content):
    client, completions = openai_client([unsupported(), completion(content)])

    with pytest.raises(LLMError):
        await client.chat_structured("prompt", Reply)

    assert len(completions.calls) == 2


@pytest.mark.asyncio
async def test_authentication_failure_during_fallback_latches_client():
    client, completions = openai_client([unsupported(), StatusError(401)])

    with pytest.raises(LLMAuthenticationError):
        await client.chat_structured("prompt", Reply)
    with pytest.raises(LLMAuthenticationError):
        await client.chat("must-not-call-sdk")

    assert len(completions.calls) == 2


@pytest.mark.asyncio
async def test_structured_retry_shares_one_wall_clock_budget():
    client, _ = openai_client([], request_timeout_sec=0.12)
    fallback_elapsed = []

    class SlowCompletions(FakeCompletions):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                await asyncio.sleep(0.08)
                raise unsupported()
            started = asyncio.get_running_loop().time()
            try:
                await asyncio.sleep(1)
                return completion('{"value": 9}')
            finally:
                fallback_elapsed.append(asyncio.get_running_loop().time() - started)

    completions = SlowCompletions([])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(LLMError, match="硬超时"):
        await client.chat_structured("prompt", Reply)

    assert len(completions.calls) == 2
    assert fallback_elapsed[0] < 0.09  # A reset 0.12s budget would exceed this bound.


@pytest.mark.parametrize("base_changed", [False, True])
@pytest.mark.parametrize("environment_key", [None, "environment-key"])
@pytest.mark.parametrize("override_key", [None, "override-key"])
def test_generic_key_precedence_matrix(tmp_path, monkeypatch, base_changed, environment_key, override_key):
    path = write_config(tmp_path, api_key="yaml-key")
    if environment_key is not None:
        monkeypatch.setenv("LLM_API_KEY", environment_key)
    llm_override = {}
    if base_changed:
        llm_override["base_url"] = "https://changed.invalid/v1"
    if override_key is not None:
        llm_override["api_key"] = override_key

    config = load_config(path, overrides={"llm": llm_override}).llm

    expected = override_key or environment_key or (None if base_changed else "yaml-key")
    assert config.api_key == expected


@pytest.mark.parametrize("shape", ["wrong", [], 1, None])
def test_request_options_rejects_invalid_root_shapes(shape):
    with pytest.raises(ValidationError, match="request_options 必须是映射"):
        LLMConfig(request_options=shape)


@pytest.mark.parametrize("shape", ["wrong", [], 1, None])
def test_request_options_rejects_invalid_extra_body_shapes(shape):
    with pytest.raises(ValidationError, match="extra_body.*必须是映射"):
        LLMConfig(request_options={"extra_body": shape})


@pytest.mark.parametrize("build", ["construct", "copy"])
@pytest.mark.parametrize("field", ["model", "base_url"])
def test_from_config_revalidates_generic_connection_boundaries(monkeypatch, build, field):
    import research_tool.infrastructure.llm.openai_client as openai_module

    monkeypatch.setattr(openai_module, "OpenAILLMClient", lambda config: config)
    if build == "construct":
        values = generic_config().model_dump()
        values[field] = " "
        config = LLMConfig.model_construct(**values)
    else:
        config = generic_config().model_copy(update={field: None})

    with pytest.raises(ValueError, match=field):
        LLMClient.from_config(config)


@pytest.mark.asyncio
async def test_request_options_nested_values_are_copied_before_provider_mutation():
    options = {"extra_body": {"thinking": {"type": "disabled"}, "tags": ["first"]}}
    client, _ = openai_client([], request_options=options)

    class MutatingCompletions:
        async def create(self, **kwargs):
            kwargs["extra_body"]["thinking"]["type"] = "changed"
            kwargs["extra_body"]["tags"].append("second")
            return completion("answer")

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=MutatingCompletions()))

    assert await client.chat("prompt") == "answer"
    assert client.config.request_options == {
        "extra_body": {"thinking": {"type": "disabled"}, "tags": ["first"]}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": {"code": "unsupported_parameter", "param": "response_format"}},
    {"error": {"code": "unsupported_value", "param": "response_format", "message": "json_object is not supported"}},
    {"error": {"code": "unsupported_parameter", "param": "response_format", "message": "Unknown parameter"}},
])
async def test_structured_fallback_uses_structured_capability_fields(body):
    error = StatusError(400, "opaque provider error", body=body)
    client, completions = openai_client([error, completion('{"value": 7}')])

    assert (await client.chat_structured("prompt", Reply)).value == 7
    assert len(completions.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": {"code": "unsupported_parameter", "param": "temperature", "message": "response_format is not supported"}},
    {"error": {"code": "invalid_api_key", "param": "response_format", "message": "unsupported parameter"}},
    {"error": {"code": "insufficient_quota", "param": "response_format", "message": "unsupported parameter"}},
    {"error": {"code": "invalid_value", "param": "response_format", "message": "invalid value"}},
    {"error": {"code": "request_timeout", "param": "response_format", "message": "unsupported parameter"}},
])
async def test_structured_fields_take_precedence_over_ambiguous_error_text(body):
    error = StatusError(400, "response_format is not supported", body=body)
    client, completions = openai_client([error, completion('{"value": 7}')])

    with pytest.raises(LLMError):
        await client.chat_structured("prompt", Reply)

    assert len(completions.calls) == 1


def test_pipeline_fingerprint_excludes_api_key_and_persisted_state_has_no_secret(tmp_path):
    from research_tool.application.pipeline import ResearchPipeline
    from research_tool.domain.models import PipelineConfig
    from research_tool.nine_loop.runtime_state import RuntimeState

    fingerprints = []
    for key in ("first-fixture-key", "second-fixture-key"):
        config = PipelineConfig(topic="fingerprint", work_dir=tmp_path, llm=generic_config(api_key=key))
        pipeline = ResearchPipeline(config)
        pipeline.topic = config.topic
        pipeline.root = tmp_path / "fingerprint"
        pipeline.runtime = RuntimeState(pipeline.root)
        pipeline._initialize()
        fingerprints.append(pipeline._state["input_fingerprint"])
        assert key not in pipeline.runtime.state_path.read_text(encoding="utf-8")

    assert fingerprints[0] == fingerprints[1]


@pytest.mark.parametrize("update", [
    {"provider": "openai"},
    {"model": "different-model"},
    {"base_url": "https://different.invalid/v1"},
    {"request_options": {"extra_body": {"thinking": {"type": "disabled"}}}},
])
def test_pipeline_fingerprint_tracks_llm_identity_options(tmp_path, update):
    from research_tool.application.pipeline import ResearchPipeline
    from research_tool.domain.models import PipelineConfig
    from research_tool.nine_loop.runtime_state import RuntimeState

    fingerprints = []
    for options in ({}, update):
        config = PipelineConfig(topic="fingerprint", work_dir=tmp_path, llm=generic_config(**options))
        pipeline = ResearchPipeline(config)
        pipeline.topic = config.topic
        pipeline.root = tmp_path / "fingerprint"
        pipeline.runtime = RuntimeState(pipeline.root)
        pipeline._initialize()
        fingerprints.append(pipeline._state["input_fingerprint"])

    assert fingerprints[0] != fingerprints[1]
