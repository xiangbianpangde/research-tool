"""配置加载与校验。

依据：
- 04-配置结构设计.md §2 环境变量、§3 优先级、§5 校验
- 03-Python库接口设计.md §6 load_config

优先级（高→低）：命令行参数 > 环境变量 > config.yaml > 代码默认值。
命令行覆盖由 CLI 层负责（构造 override dict 传入 load_config）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ConfigValidationError
from .models import PipelineConfig

# config.yaml 查找顺序
_DEFAULT_PATHS = [
    Path("./config.yaml"),
    Path.home() / ".research" / "config.yaml",
]

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 顶层环境变量覆盖（04 §2 可选环境变量）
_ENV_OVERRIDES = {
    "RESEARCH_WORK_DIR": ("pipeline", "work_dir"),
    "LLM_PROVIDER": ("llm", "provider"),
    "LLM_MODEL": ("llm", "model"),
    "LLM_BASE_URL": ("llm", "base_url"),
}


def _resolve_env(value: Any) -> Any:
    """递归把 ${VAR} 替换为环境变量值。未定义的变量替换为 None（整串即为引用时）
    或空串（嵌在文本中时）。"""
    if isinstance(value, str):
        if _ENV_REF.fullmatch(value):
            var = _ENV_REF.fullmatch(value).group(1)
            return os.environ.get(var)
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    """override 覆盖 base，dict 递归合并，None 值跳过（视为未设置）。"""
    out = dict(base)
    for k, v in override.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _find_config(path: str | Path | None) -> Path | None:
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigValidationError(f"指定的配置文件不存在: {p}")
        return p
    if env := os.environ.get("RESEARCH_CONFIG"):
        return Path(env)
    for candidate in _DEFAULT_PATHS:
        if candidate.exists():
            return candidate
    return None


def _flatten_to_pipeline(raw: dict) -> dict:
    """把 config.yaml 的分段结构映射为 PipelineConfig 的字段。

    yaml 用 `collector:`/`cleaner:` 等段名，PipelineConfig 同名字段；
    `pipeline:` 段下的 work_dir/stages 提升到顶层。
    """
    data: dict[str, Any] = {}
    for src, dst in [
        ("llm", "llm"),
        ("collector", "collector"),
        ("cleaner", "cleaner"),
        ("extractor", "extractor"),
        ("organizer", "organizer"),
        ("reporter", "reporter"),
    ]:
        if src in raw and raw[src] is not None:
            data[dst] = raw[src]

    pipeline = raw.get("pipeline") or {}
    if "work_dir" in pipeline:
        data["work_dir"] = pipeline["work_dir"]
    if "stages" in pipeline:
        data["stages"] = pipeline["stages"]
    if "resume" in pipeline:
        data["resume"] = pipeline["resume"]
    if "topic" in raw:
        data["topic"] = raw["topic"]
    return data


def _apply_env_overrides(data: dict) -> dict:
    for env_var, (section, key) in _ENV_OVERRIDES.items():
        if (val := os.environ.get(env_var)) is not None:
            if section == "pipeline":
                data[key] = val
            else:
                data.setdefault(section, {})[key] = val
    return data


# provider → 默认读取的 API Key 环境变量（04 §2）
_PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _apply_secret_env(data: dict) -> dict:
    """未显式配置 api_key 时，按 provider 从环境变量补齐，实现零 config.yaml 启动。"""
    llm = data.setdefault("llm", {})
    if not llm.get("api_key"):
        provider = llm.get("provider", "deepseek")
        env_name = _PROVIDER_KEY_ENV.get(provider)
        if env_name and os.environ.get(env_name):
            llm["api_key"] = os.environ[env_name]
    if os.environ.get("TAVILY_API_KEY"):
        col = data.setdefault("collector", {})
        if not col.get("tavily_api_key"):
            col["tavily_api_key"] = os.environ["TAVILY_API_KEY"]
    return data


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict | None = None,
) -> PipelineConfig:
    """加载并校验配置，返回 PipelineConfig。

    Args:
        path: 配置文件路径；None 时按 RESEARCH_CONFIG → ./config.yaml →
              ~/.research/config.yaml 查找。全部缺失则使用纯默认值。
        overrides: 命令行层传入的覆盖（最高优先级），结构同 PipelineConfig 字段。
    """
    raw: dict = {}
    cfg_path = _find_config(path)
    if cfg_path is not None:
        try:
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"YAML 解析失败 ({cfg_path}): {e}") from e
        raw = _resolve_env(loaded)

    data = _flatten_to_pipeline(raw)
    data = _apply_env_overrides(data)
    data = _apply_secret_env(data)
    if overrides:
        data = _deep_merge(data, overrides)

    try:
        return PipelineConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigValidationError(
            f"配置校验失败：\n{e}\n\n请检查 config.yaml；参考 config.example.yaml。"
        ) from e
