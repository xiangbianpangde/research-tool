from pathlib import Path

import pytest

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import PipelineConfig


@pytest.fixture(autouse=True)
def _isolate_local_deployment_env(monkeypatch):
    """Keep config tests deterministic after `research setup` writes a real .env."""
    for name in (
        "RESEARCH_CONFIG",
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
    ):
        monkeypatch.delenv(name, raising=False)


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


def test_talk_section_from_yaml_not_dropped(tmp_path):
    """config.yaml 的 talk: 段必须进入 PipelineConfig（阶段 F / P14 回归）。

    历史 bug：`_flatten_to_pipeline` 未登记 talk，导致 enabled/max_talks 等
    静默回落到默认值；CLI --with-talks 覆盖路径不受影响，仅 yaml 路径坏。
    """
    p = _write(
        tmp_path,
        """
llm:
  provider: deepseek
  api_key: sk-test
talk:
  enabled: true
  max_talks: 3
  min_title_similarity: 0.55
  prefer_official_channel: false
  ingest: true
  conference: ICCV
  search_results_per_paper: 7
pipeline:
  work_dir: out
""",
    )
    cfg = load_config(p)
    assert cfg.talk.enabled is True
    assert cfg.talk.max_talks == 3
    assert cfg.talk.min_title_similarity == pytest.approx(0.55)
    assert cfg.talk.prefer_official_channel is False
    assert cfg.talk.ingest is True
    assert cfg.talk.conference == "ICCV"
    assert cfg.talk.search_results_per_paper == 7


@pytest.mark.parametrize(
    ("mode", "expected_stages", "expected_rounds", "expected_deep_search", "expected_backward"),
    [
        (
            "fast",
            ["collect", "clean", "extract", "organize", "report"],
            1,
            False,
            0,
        ),
        (
            "standard",
            ["collect", "deepen", "clean", "extract", "organize", "report"],
            1,
            False,
            0,
        ),
        (
            "deep",
            ["collect", "deepen", "clean", "extract", "organize", "report"],
            2,
            True,
            1,
        ),
    ],
)
def test_research_mode_applies_cost_defaults(
    tmp_path,
    monkeypatch,
    mode,
    expected_stages,
    expected_rounds,
    expected_deep_search,
    expected_backward,
):
    monkeypatch.chdir(tmp_path)

    cfg = load_config(overrides={"mode": mode})

    assert cfg.mode == mode
    assert cfg.stages == expected_stages
    assert cfg.collector.search_rounds == expected_rounds
    assert cfg.collector.deep_search is expected_deep_search
    assert cfg.max_backward_rounds == expected_backward


def test_mode_defaults_do_not_override_explicit_config(tmp_path):
    path = _write(
        tmp_path,
        """
pipeline:
  mode: deep
  max_backward_rounds: 0
collector:
  search_rounds: 3
  deep_search: false
deepen:
  profile_iterations: 4
""",
    )

    cfg = load_config(path)

    assert cfg.mode == "deep"
    assert cfg.collector.search_rounds == 3
    assert cfg.collector.deep_search is False
    assert cfg.deepen.profile_iterations == 4
    assert cfg.max_backward_rounds == 0


def test_example_config_keeps_default_stage_order():
    example = Path(__file__).resolve().parents[2] / "docs" / "config.example.yaml"

    cfg = load_config(example)

    assert cfg.stages == PipelineConfig().stages
