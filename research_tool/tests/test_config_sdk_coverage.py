"""Offline behavior coverage for configuration and the public SDK helpers."""

from __future__ import annotations

import builtins
import importlib.util
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

import research_tool
from research_tool.domain import config as config_module
from research_tool.domain.errors import ConfigValidationError, LLMAuthenticationError
from research_tool.domain.models import ExpertEntry, ExpertLibrary
from research_tool.infrastructure.experts.registry import ExpertRegistry
from research_tool.infrastructure.llm.mock import MockLLMClient
from research_tool.infrastructure.stages.base import (
    domain_of,
    ensure_dir,
    has_output,
    read_json,
    safe_filename,
    write_json,
    write_text,
)


def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RESEARCH_CONFIG",
        "RESEARCH_WORK_DIR",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MINIMAX_API_KEY",
        "TAVILY_API_KEY",
        "GITHUB_TOKEN",
        "S2_API_KEY",
        "OPENALEX_MAILTO",
        "GROQ_API_KEY",
        "HTTPS_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_local_deployment_env(monkeypatch):
    """Keep SDK config tests independent from the developer's deployed .env."""
    _clear_config_env(monkeypatch)


def test_override_provider_selects_matching_secret(monkeypatch, tmp_path):
    """RED regression: CLI provider overrides must not retain another provider's key."""
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret-that-is-intentionally-longer")

    cfg = config_module.load_config(overrides={"llm": {"provider": "minimax"}})

    assert cfg.llm.provider == "minimax"
    assert cfg.llm.api_key == "minimax-secret-that-is-intentionally-longer"


def test_explicit_minimax_key_beats_longer_environment_key(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n  provider: minimax\n  api_key: explicit-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "environment-key-that-is-much-longer-than-explicit")

    cfg = config_module.load_config(path)

    assert cfg.llm.api_key == "explicit-key"


def test_provider_override_discards_yaml_key_from_previous_provider(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n  provider: deepseek\n  api_key: deepseek-yaml-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-environment-key")

    cfg = config_module.load_config(path, overrides={"llm": {"provider": "minimax"}})

    assert cfg.llm.api_key == "minimax-environment-key"


def test_minimax_never_uses_anthropic_vendor_secret(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-anthropic-vendor-secret")

    cfg = config_module.load_config(overrides={"llm": {"provider": "minimax"}})

    assert cfg.llm.api_key is None


@pytest.mark.parametrize("section", ["llm", "collector"])
def test_non_mapping_override_sections_are_config_errors(monkeypatch, tmp_path, section):
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigValidationError, match=section):
        config_module.load_config(overrides={section: "bad"})


@pytest.mark.parametrize("overrides", ["bad", 3, []])
def test_non_mapping_overrides_root_is_config_error(monkeypatch, tmp_path, overrides):
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigValidationError, match="overrides"):
        config_module.load_config(overrides=overrides)


def test_research_config_missing_path_is_config_error(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("RESEARCH_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigValidationError, match="不存在"):
        config_module.load_config()


def test_explicit_missing_config_path_is_config_error(tmp_path):
    with pytest.raises(ConfigValidationError, match="不存在"):
        config_module.load_config(tmp_path / "missing.yaml")


def test_yaml_root_must_be_mapping(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="映射"):
        config_module.load_config(path)

    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="映射"):
        config_module.load_config(path)

    path.write_text("\n", encoding="utf-8")
    assert config_module.load_config(path).topic == ""


@pytest.mark.parametrize("section", ["llm", "collector", "pipeline"])
def test_non_mapping_config_sections_are_config_errors(tmp_path, section):
    path = tmp_path / "bad-section.yaml"
    path.write_text(f"{section}: bad\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=section):
        config_module.load_config(path)


def test_config_read_os_error_is_wrapped(tmp_path):
    directory = tmp_path / "config-directory"
    directory.mkdir()

    with pytest.raises(ConfigValidationError, match="读取"):
        config_module.load_config(directory)


def test_validation_error_does_not_echo_secret_input(tmp_path):
    path = tmp_path / "secret.yaml"
    path.write_text(
        "llm:\n  provider: anthropic\n  api_key: [DO-NOT-ECHO]\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError) as exc_info:
        config_module.load_config(path)

    assert "DO-NOT-ECHO" not in str(exc_info.value)
    assert "DO-NOT-ECHO" not in "".join(traceback.format_exception(exc_info.value))


def test_yaml_error_traceback_does_not_echo_secret_line(tmp_path):
    path = tmp_path / "malformed-secret.yaml"
    path.write_text("llm:\n  api_key: [YAML-SECRET\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError) as exc_info:
        config_module.load_config(path)

    assert "YAML-SECRET" not in "".join(traceback.format_exception(exc_info.value))


def test_config_resolution_merge_and_all_environment_secrets(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_WORK_DIR", "env-output")
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_MODEL", "MiniMax-M2")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.minimax.example/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "short-anthropic")
    monkeypatch.setenv("MINIMAX_API_KEY", "preferred-minimax-key-that-is-much-longer")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test")
    monkeypatch.setenv("S2_API_KEY", "s2-test")
    monkeypatch.setenv("OPENALEX_MAILTO", "test@example.com")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("EMBEDDED", "value")
    path = tmp_path / "chosen.yaml"
    path.write_text(
        "topic: ${EMBEDDED}-topic\n"
        'collector:\n  extra_queries: ["${EMBEDDED}", fixed]\n'
        "pipeline:\n  resume: false\n  max_backward_rounds: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_CONFIG", str(path))

    cfg = config_module.load_config(
        overrides={"collector": {"depth": 3, "proxy": None}, "llm": {"model": None}}
    )

    assert cfg.topic == "value-topic"
    assert cfg.work_dir == Path("env-output")
    assert cfg.resume is False and cfg.max_backward_rounds == 2
    assert cfg.llm.provider == "minimax" and cfg.llm.model == "MiniMax-M2"
    assert cfg.llm.api_key == "preferred-minimax-key-that-is-much-longer"
    assert cfg.collector.depth == 3
    assert cfg.collector.extra_queries == ["value", "fixed"]
    assert cfg.collector.github_token == os.environ["GITHUB_TOKEN"]
    assert cfg.collector.semantic_scholar_api_key == "s2-test"
    assert cfg.collector.openalex_mailto == "test@example.com"
    assert cfg.collector.proxy == "http://proxy.example:8080"


def test_default_path_and_yaml_parse_error(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    good = tmp_path / "default.yaml"
    good.write_text("pipeline:\n  work_dir: default-output\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "_DEFAULT_PATHS", [good])
    assert config_module.load_config().work_dir == Path("default-output")

    bad = tmp_path / "bad.yaml"
    bad.write_text("llm: [", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="YAML 解析失败"):
        config_module.load_config(bad)


def test_secret_helper_fills_nested_video_sections(monkeypatch):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test")
    data = {"llm": {"provider": "anthropic"}, "video": {}, "transcriber": {}}

    resolved = config_module._apply_secret_env(data)

    assert resolved["llm"]["api_key"] == "anthropic-test"
    for section in ("video", "transcriber"):
        assert resolved[section]["groq_api_key"] == "groq-test"
        assert resolved[section]["minimax_api_key"] == "minimax-test"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, "chosen-model"])
async def test_public_research_helper_builds_and_runs_pipeline(monkeypatch, model):
    captured: dict = {}
    sentinel = object()

    def fake_load(path, *, overrides):
        captured["path"] = path
        captured["overrides"] = overrides
        return SimpleNamespace(topic=overrides["topic"])

    class FakePipeline:
        def __init__(self, config):
            captured["config"] = config

        async def run(self, topic):
            captured["run_topic"] = topic
            return sentinel

    monkeypatch.setattr(research_tool, "load_config", fake_load)
    monkeypatch.setattr(research_tool, "ResearchPipeline", FakePipeline)

    result = await research_tool.research(
        "topic", work_dir="output", llm_provider="minimax", llm_model=model, config_path="cfg"
    )

    assert result is sentinel and captured["run_topic"] == "topic"
    assert captured["overrides"]["llm"]["provider"] == "minimax"
    assert ("model" in captured["overrides"]["llm"]) is (model is not None)


@pytest.mark.asyncio
async def test_public_quick_collect_returns_serialized_sources(monkeypatch, tmp_path):
    captured: dict = {}

    class Source:
        def model_dump(self):
            return {"url": "https://example.com/paper"}

    class FakeCollector:
        def __init__(self, config):
            captured["config"] = config

        async def run(self, topic, path):
            captured["topic"] = topic
            captured["path"] = path
            return SimpleNamespace(sources=[Source()])

    monkeypatch.setattr(research_tool, "Collector", FakeCollector)
    result = await research_tool.quick_collect("topic", max_results=3, work_dir=tmp_path)

    assert result == [{"url": "https://example.com/paper"}]
    assert captured["config"].max_results_per_engine == 3
    assert captured["path"] == tmp_path / "quick"


def test_package_import_best_effort_when_dotenv_import_fails(monkeypatch):
    """The public package must still import when optional dotenv is broken."""
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "dotenv":
            raise RuntimeError("broken optional dotenv")
        return original_import(name, *args, **kwargs)

    alias = "research_tool._coverage_import_probe"
    spec = importlib.util.spec_from_file_location(alias, Path(research_tool.__file__))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
        assert module.__version__ == research_tool.__version__
    finally:
        sys.modules.pop(alias, None)


@pytest.mark.asyncio
async def test_translate_empty_input_skips_llm():
    from research_tool.common.translate import translate_markdown

    llm = MockLLMClient(chat_response="unexpected")
    assert await translate_markdown("", llm) == ""
    assert llm.calls == []


@pytest.mark.asyncio
async def test_mock_llm_default_structured_and_stream():
    from pydantic import BaseModel

    class Empty(BaseModel):
        value: str = "default"

    llm = MockLLMClient(chat_response="one two")
    await llm.healthcheck()
    assert (await llm.chat_structured("prompt", Empty)).value == "default"
    assert [token async for token in llm.stream("prompt")] == ["one ", "two "]

    supplied = Empty(value="supplied")
    assert (await MockLLMClient(structured_response=supplied).chat_structured("p", Empty)) is supplied
    callback = MockLLMClient(structured_response=lambda prompt, schema: schema(value=prompt))
    assert (await callback.chat_structured("callback", Empty)).value == "callback"


def test_registry_skips_entries_without_domains_or_github():
    entries = [
        ExpertEntry(id="empty", domains=[]),
        ExpertEntry(id="match", domains=["vision"]),
    ]
    registry = ExpertRegistry(ExpertLibrary(experts=entries))
    assert [item.id for item in registry.match("vision")] == ["match"]
    assert registry.scoped_queries_for(entries, " vision ") == []


def test_stage_base_invalid_url_and_absent_output(tmp_path):
    assert domain_of("http://[invalid") == "unknown"
    assert domain_of("https://www.Example.com:443/path") == "example.com"
    assert domain_of("not a url") == "unknown"
    assert has_output(tmp_path / "absent", ["*.json"]) is False

    directory = ensure_dir(tmp_path / "nested")
    json_path = directory / "data.json"
    write_json(json_path, {"text": "中文"})
    assert read_json(json_path) == {"text": "中文"}
    text_path = directory / "note.txt"
    write_text(text_path, "hello")
    assert text_path.read_text(encoding="utf-8") == "hello"
    assert safe_filename('  Bad:/Name*?  ') == "bad-name"
    assert safe_filename("... ") == "file"
    assert has_output(directory, ["*.missing"]) is False
    assert has_output(directory, ["*.missing", "*.json"]) is True


@pytest.mark.asyncio
async def test_translate_authentication_failure_is_not_downgraded():
    from research_tool.common.translate import translate_markdown

    class AuthFailure(MockLLMClient):
        async def chat(self, *args, **kwargs):
            raise LLMAuthenticationError("401")

    with pytest.raises(LLMAuthenticationError):
        await translate_markdown("content", AuthFailure())
