from pathlib import Path

import pytest

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import PipelineConfig


EXPECTED_STAGES = [
    "collect", "clean", "extract", "knowledge", "inspect",
    "targeted", "merge", "qgate", "report",
]


@pytest.fixture(autouse=True)
def _isolate_local_deployment_env(monkeypatch):
    """Keep config tests deterministic after `research setup` writes a real .env."""
    monkeypatch.setattr("research_tool.domain.config._DEFAULT_PATHS", [])
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
    assert cfg.stages == EXPECTED_STAGES


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
    ("mode", "expected_rounds", "expected_attempts"),
    [("full", 1, 3), ("standard", 1, 2), ("deep", 2, 2)],
)
def test_research_mode_applies_cost_defaults(
    tmp_path, monkeypatch, mode, expected_rounds, expected_attempts,
):
    monkeypatch.chdir(tmp_path)

    cfg = load_config(overrides={"mode": mode})

    assert cfg.mode == mode
    assert cfg.stages == EXPECTED_STAGES
    assert cfg.collector.search_rounds == expected_rounds
    assert cfg.llm_stage_attempts == expected_attempts
    assert cfg.extractor.enabled is True
    assert cfg.extractor.fail_on_chunk_error is True


@pytest.mark.parametrize("mode", ["brief", "fast"])
def test_removed_research_modes_are_config_errors(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigValidationError, match=mode):
        load_config(overrides={"mode": mode})


@pytest.mark.parametrize("source", ["yaml", "overrides"])
def test_legacy_deepen_config_is_rejected(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, "deepen:\n  profile_iterations: 4\n") if source == "yaml" else None
    overrides = {"deepen": {"profile_iterations": 4}} if source == "overrides" else None

    with pytest.raises(ConfigValidationError, match="deepen"):
        load_config(path, overrides=overrides)


def test_mode_defaults_do_not_override_explicit_config(tmp_path):
    path = _write(
        tmp_path,
        """
pipeline:
  mode: deep
  llm_stage_attempts: 4
collector:
  search_rounds: 3
  deep_search: false
nine_loop:
  max_rounds: 4
  wall_s_max: 120
""",
    )

    cfg = load_config(path)

    assert cfg.mode == "deep"
    assert cfg.collector.search_rounds == 3
    assert cfg.collector.deep_search is False
    assert cfg.llm_stage_attempts == 4
    assert cfg.nine_loop.max_rounds == 4
    assert cfg.nine_loop.wall_s_max == 120
    assert cfg.stages == EXPECTED_STAGES


def test_example_config_keeps_default_stage_order():
    example = Path(__file__).resolve().parents[2] / "config" / "example.yaml"

    cfg = load_config(example)

    assert cfg.stages == EXPECTED_STAGES == PipelineConfig().stages
    assert cfg.nine_loop.model_dump() == {
        "max_rounds": 2,
        "max_high_findings": 0,
        "max_total_findings": 0,
        "wall_s_max": 1800,
    }
