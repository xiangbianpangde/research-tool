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
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import ConfigValidationError
from .models import PipelineConfig

_STANDARD_STAGES = ["collect", "deepen", "clean", "extract", "organize", "report"]
_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "brief": {
        "mode": "brief",
        "stages": ["collect", "clean", "report"],
        "collector": {"search_rounds": 1, "deep_search": False},
        "deepen": {"profile_iterations": 1},
        "max_backward_rounds": 0,
        "llm_stage_attempts": 2,
    },
    "full": {
        "mode": "full",
        "stages": list(_STANDARD_STAGES),
        "collector": {"search_rounds": 1, "deep_search": False},
        "deepen": {"profile_iterations": 1},
        "extractor": {"enabled": True, "fail_on_chunk_error": True},
        "max_backward_rounds": 0,
        "llm_stage_attempts": 3,
    },
    "fast": {
        "mode": "fast",
        "stages": [stage for stage in _STANDARD_STAGES if stage != "deepen"],
        "collector": {"search_rounds": 1, "deep_search": False},
        "deepen": {"profile_iterations": 1},
        "max_backward_rounds": 0,
    },
    "standard": {
        "mode": "standard",
        "stages": list(_STANDARD_STAGES),
        "collector": {"search_rounds": 1, "deep_search": False},
        "deepen": {"profile_iterations": 1},
        "max_backward_rounds": 0,
    },
    "deep": {
        "mode": "deep",
        "stages": list(_STANDARD_STAGES),
        "collector": {
            "search_rounds": 2,
            "deep_search": True,
            "deep_pages": 2,
            "deep_sorts": ["relevance", "date"],
        },
        "deepen": {"profile_iterations": 2},
        "max_backward_rounds": 1,
    },
}

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
        if match := _ENV_REF.fullmatch(value):
            var = match.group(1)
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


def mode_defaults(mode: str) -> dict[str, Any]:
    """Return a fresh cost/quality preset without sharing mutable values."""
    preset = _MODE_DEFAULTS.get(mode)
    if preset is None:
        raise ConfigValidationError(
            f"未知调研模式: {mode}（推荐 brief/full；兼容 fast/standard/deep）"
        )
    return deepcopy(preset)


def _find_config(path: str | Path | None) -> Path | None:
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigValidationError(f"指定的配置文件不存在: {p}")
        return p
    if env := os.environ.get("RESEARCH_CONFIG"):
        p = Path(env)
        if not p.exists():
            raise ConfigValidationError(f"RESEARCH_CONFIG 指向的配置文件不存在: {p}")
        return p
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
        ("deepen", "deepen"),
        ("pdf_ingest", "pdf_ingest"),
        ("cleaner", "cleaner"),
        ("extractor", "extractor"),
        ("organizer", "organizer"),
        ("reporter", "reporter"),
        # 阶段 F：论文 ↔ YouTube talk；遗漏会导致 config.yaml talk: 段静默丢弃
        ("talk", "talk"),
        # P3 预留：("bilinote", "bilinote") —— 待 BiliNoteConfig 落地后启用
    ]:
        if src in raw and raw[src] is not None:
            data[dst] = raw[src]

    pipeline = raw.get("pipeline") or {}
    # 把 yaml 的 pipeline 段下字段提升到 PipelineConfig 顶层；新增 PipelineConfig
    # 字段必须在这里登记，否则 yaml 值被静默丢弃（如 P2-6 的 max_backward_rounds）
    for key in (
        "mode",
        "work_dir",
        "stages",
        "resume",
        "max_backward_rounds",
        "llm_stage_attempts",
        "llm_retry_backoff_sec",
    ):
        if key in pipeline:
            data[key] = pipeline[key]
    if "topic" in raw:
        data["topic"] = raw["topic"]
    return data


def _apply_env_overrides(data: dict) -> dict:
    for env_var, (section, key) in _ENV_OVERRIDES.items():
        if (val := os.environ.get(env_var)) is not None:
            if section == "pipeline":
                data[key] = val
            else:
                data.setdefault(section, {})[key] = None if key == "base_url" and not val else val
    return data


# provider → 默认读取的 API Key 环境变量（04 §2）
_PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}

_MAPPING_SECTIONS = (
    "llm",
    "collector",
    "deepen",
    "pdf_ingest",
    "cleaner",
    "extractor",
    "organizer",
    "reporter",
    "talk",
    "pipeline",
)


def _validate_mapping_sections(raw: dict[str, Any]) -> None:
    for section in _MAPPING_SECTIONS:
        value = raw.get(section)
        if value is not None and not isinstance(value, dict):
            raise ConfigValidationError(f"配置段 {section} 必须是键值映射")


def _llm_identity(data: dict[str, Any]) -> tuple[object, object]:
    llm = data.get("llm")
    if not isinstance(llm, dict):
        return None, None
    return llm.get("provider"), llm.get("base_url")


def _drop_stale_llm_key(
    data: dict[str, Any],
    previous_identity: tuple[object, object],
    *,
    explicit_replacement: bool = False,
) -> dict[str, Any]:
    llm = data.get("llm")
    if (
        explicit_replacement
        or not isinstance(llm, dict)
        or _llm_identity(data) == previous_identity
    ):
        return data
    return {**data, "llm": {key: value for key, value in llm.items() if key != "api_key"}}


def _apply_secret_env(data: dict) -> dict:
    """未显式配置 api_key 时，按 provider 从环境变量补齐，实现零 config.yaml 启动。"""
    llm = data.setdefault("llm", {})
    provider = llm.get("provider", "deepseek")
    base_url = str(llm.get("base_url") or "")
    if not llm.get("api_key"):
        if provider == "minimax" or "minimax" in base_url.lower():
            # 协议兼容不代表凭据兼容，绝不能把真实 Anthropic 厂商密钥
            # 静默发送给 MiniMax 端点。
            key = os.environ.get("MINIMAX_API_KEY")
        else:
            env_name = _PROVIDER_KEY_ENV.get(provider)
            key = os.environ.get(env_name) if env_name else None
        if key:
            llm["api_key"] = key
    col = data.setdefault("collector", {})
    # 各搜索相关密钥：.env → 环境变量 → 补进 config（yaml 里 ${EMPTY} 可能是空串）
    def _fill(cfg_key: str, env_name: str) -> None:
        cur = col.get(cfg_key)
        if cur is None or (isinstance(cur, str) and not cur.strip()):
            if os.environ.get(env_name):
                col[cfg_key] = os.environ[env_name]

    _fill("tavily_api_key", "TAVILY_API_KEY")
    _fill("github_token", "GITHUB_TOKEN")
    _fill("semantic_scholar_api_key", "S2_API_KEY")
    _fill("openalex_mailto", "OPENALEX_MAILTO")

    # 视频：嵌套配置段若存在则从环境补齐（MiniMax 优先，Groq 可选 fallback）
    for section in ("video", "video_ingest", "transcriber"):
        sec = data.get(section)
        if isinstance(sec, dict):
            if not sec.get("groq_api_key") and os.environ.get("GROQ_API_KEY"):
                sec["groq_api_key"] = os.environ["GROQ_API_KEY"]
            if not sec.get("minimax_api_key"):
                mk = os.environ.get("MINIMAX_API_KEY")
                if mk:
                    sec["minimax_api_key"] = mk

    # 代理 fallback
    if os.environ.get("HTTPS_PROXY"):
        if not col.get("proxy"):
            col["proxy"] = os.environ["HTTPS_PROXY"]
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
    if overrides is not None and not isinstance(overrides, dict):
        raise ConfigValidationError("overrides 根节点必须是键值映射")
    raw: dict = {}
    cfg_path = _find_config(path)
    if cfg_path is not None:
        try:
            text = cfg_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise ConfigValidationError(f"配置文件读取失败 ({cfg_path}): {e}") from e
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            # PyYAML 的异常上下文会包含原始行，配置行可能正好是密钥。
            raise ConfigValidationError(f"YAML 解析失败: {cfg_path}") from None
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigValidationError(f"配置文件根节点必须是键值映射: {cfg_path}")
        raw = _resolve_env(loaded)
        _validate_mapping_sections(raw)

    flattened = _flatten_to_pipeline(raw)
    override_mode = overrides.get("mode") if isinstance(overrides, dict) else None
    selected_mode = str(override_mode or flattened.get("mode") or "full")
    data = _deep_merge(mode_defaults(selected_mode), flattened)
    previous_identity = _llm_identity(data)
    data = _apply_env_overrides(data)
    data = _drop_stale_llm_key(data, previous_identity)
    if overrides:
        previous_identity = _llm_identity(data)
        data = _deep_merge(data, overrides)
        override_llm = overrides.get("llm")
        explicit_key = isinstance(override_llm, dict) and override_llm.get("api_key") is not None
        data = _drop_stale_llm_key(
            data,
            previous_identity,
            explicit_replacement=explicit_key,
        )
    if data.get("mode") == "full":
        # full 是产物契约，不允许 config.yaml 或 CLI 静默裁掉中间阶段。
        data = _deep_merge(
            data,
            {
                "stages": list(_STANDARD_STAGES),
                "deepen": {"enabled": True},
                "extractor": {"enabled": True, "fail_on_chunk_error": True},
            },
        )
    _validate_mapping_sections(data)
    # 密钥必须根据最终 provider/base_url 选择；CLI overrides 可能刚切换
    # provider，若提前注入会把旧 provider 的 key 带到新端点并触发 401。
    data = _apply_secret_env(data)

    try:
        return PipelineConfig.model_validate(data)
    except ValidationError as e:
        details = []
        for error in e.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in error.get("loc", ())) or "root"
            details.append(f"- {location}: {error.get('msg', '无效值')}")
        raise ConfigValidationError(
            "配置校验失败：\n"
            + "\n".join(details)
            + "\n\n请检查 config.yaml；参考 docs/config.example.yaml。"
        ) from None
