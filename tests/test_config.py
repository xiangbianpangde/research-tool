import pytest

from research_tool.config import load_config
from research_tool.errors import ConfigValidationError


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-secret")
    p = _write(tmp_path, "llm:\n  provider: deepseek\n  api_key: ${MY_KEY}\n")
    cfg = load_config(p)
    assert cfg.llm.api_key == "sk-secret"


def test_override_priority(tmp_path):
    p = _write(tmp_path, "llm:\n  provider: deepseek\n  model: deepseek-chat\n")
    cfg = load_config(p, overrides={"llm": {"model": "deepseek-reasoner"}})
    assert cfg.llm.model == "deepseek-reasoner"


def test_env_top_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    p = _write(tmp_path, "llm:\n  provider: deepseek\n")
    cfg = load_config(p)
    assert cfg.llm.provider == "openai"


def test_invalid_provider(tmp_path):
    p = _write(tmp_path, "llm:\n  provider: notreal\n")
    with pytest.raises(ConfigValidationError):
        load_config(p)


def test_api_key_from_env_by_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    cfg = load_config()  # 无 config.yaml，纯环境变量
    assert cfg.llm.api_key == "sk-ds"
    assert cfg.collector.tavily_api_key == "tvly-x"


def test_explicit_key_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    p = _write(tmp_path, "llm:\n  provider: deepseek\n  api_key: sk-explicit\n")
    cfg = load_config(p)
    assert cfg.llm.api_key == "sk-explicit"


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_CONFIG", raising=False)
    cfg = load_config()
    assert cfg.llm.provider == "deepseek"
    assert cfg.stages[0] == "collect"
