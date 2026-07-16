"""Declarative deployment contract shared by the setup wizard and CLI.

The profile is the single source of truth: installation and postflight both
expand the same capability set, so a selected feature cannot silently vanish
between planning and verification.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import tomllib


@dataclass(frozen=True)
class ProviderSpec:
    key_env: str
    model: str
    base_url: str


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        "DEEPSEEK_API_KEY", "deepseek-chat", "https://api.deepseek.com/v1"
    ),
    "openai": ProviderSpec("OPENAI_API_KEY", "gpt-4o-mini", ""),
    "anthropic": ProviderSpec("ANTHROPIC_API_KEY", "claude-sonnet-4-6", ""),
    "minimax": ProviderSpec(
        "MINIMAX_API_KEY", "MiniMax-M3", "https://api.minimaxi.com/v1"
    ),
}


def provider_env_updates(provider: str, api_key: str) -> dict[str, str]:
    """Return the complete runtime identity for one selected LLM provider."""
    try:
        spec = PROVIDER_SPECS[provider]
    except KeyError as exc:
        supported = ", ".join(PROVIDER_SPECS)
        raise ValueError(f"不支持的 LLM provider: {provider}; 可选: {supported}") from exc
    return {
        "LLM_PROVIDER": provider,
        "LLM_MODEL": spec.model,
        "LLM_BASE_URL": spec.base_url,
        spec.key_env: api_key,
    }


FULL_CAPABILITIES = frozenset(
    {
        "core",
        "llm",
        "search",
        "ui",
        "video",
        "pdf",
        "crawl",
        "crawl_browser",
        "ffmpeg",
        "deno",
        "opencli",
    }
)

CAPABILITY_FIXES = MappingProxyType(
    {
        "core": '重新运行：pip install -e ".[llm,search]"',
        "llm": '安装 SDK 并配置匹配的 provider/key：pip install -e ".[llm]"',
        "search": '安装默认 Web/Tavily 搜索：pip install -e ".[search]"',
        "ui": '安装 Web UI：pip install -e ".[ui]"',
        "video": '安装 .[video] 并配置有效 MINIMAX_API_KEY（默认视频链路必需）',
        "pdf": '安装 MinerU（大体积）：pip install -e ".[pdf]"',
        "crawl": '安装 Crawl4AI：pip install -e ".[crawl]"',
        "crawl_browser": "运行当前虚拟环境中的 crawl4ai-setup",
        "ffmpeg": "用系统包管理器安装 ffmpeg，并确认 ffmpeg -version 可用",
        "deno": "安装 Deno 2+，并确认 deno --version 可用",
        "opencli": "安装 OpenCLI/浏览器扩展并运行 opencli doctor",
    }
)

_EXTERNAL_COMMAND_CAPABILITIES = frozenset({"ffmpeg", "deno", "opencli"})


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    description: str
    capabilities: frozenset[str]
    extras: tuple[str, ...]

    @property
    def required_commands(self) -> tuple[str, ...]:
        return tuple(sorted(self.capabilities & _EXTERNAL_COMMAND_CAPABILITIES))


PROFILES: dict[str, ProfileSpec] = {
    "minimal": ProfileSpec(
        "最小可跑",
        "六阶段 CLI、默认 Web 搜索和全部 LLM 客户端",
        frozenset({"core", "llm", "search"}),
        ("llm", "search"),
    ),
    "recommended": ProfileSpec(
        "推荐",
        "最小档 + Web UI + Crawl4AI 浏览器抓取",
        frozenset({"core", "llm", "search", "ui", "crawl", "crawl_browser"}),
        ("llm", "search", "ui", "crawl"),
    ),
    "full": ProfileSpec(
        "完整",
        "全部 Python 能力，并严格检查 PDF、视频和 X 的系统工具",
        FULL_CAPABILITIES,
        ("llm", "search", "ui", "video", "pdf", "crawl"),
    ),
}


@dataclass(frozen=True)
class InstallStep:
    id: str
    label: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class DeploymentPlan:
    profile_key: str
    profile: ProfileSpec
    steps: tuple[InstallStep, ...]


@dataclass(frozen=True)
class DeploymentResult:
    ok: bool
    completed_steps: tuple[str, ...]
    failed_step: str | None = None
    missing_capabilities: tuple[str, ...] = ()


def build_deployment_plan(
    profile: str,
    *,
    python: str,
    project_root: Path | None = None,
) -> DeploymentPlan:
    """Build one deterministic install plan for source or wheel deployments."""
    try:
        selected = PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"未知部署档位: {profile}") from exc

    extras = ",".join(selected.extras)
    root = Path.cwd() if project_root is None else Path(project_root)
    if _is_research_tool_source(root):
        install_command = (python, "-m", "pip", "install", "-e", f".[{extras}]")
    else:
        install_command = (python, "-m", "pip", "install", f"research-tool[{extras}]")
    steps = (
        InstallStep("package", "安装所选能力", install_command),
        InstallStep("pip_check", "检查 Python 依赖闭包", (python, "-m", "pip", "check")),
    )
    if "crawl_browser" in selected.capabilities:
        python_path = Path(python)
        crawl_setup = python_path.with_name(
            "crawl4ai-setup.exe" if python_path.suffix.lower() == ".exe" else "crawl4ai-setup"
        )
        steps = (
            *steps,
            InstallStep(
                "crawl_browser",
                "安装 Crawl4AI 浏览器资产",
                (str(crawl_setup),),
            ),
        )
    return DeploymentPlan(profile, selected, steps)


def _is_research_tool_source(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file() or not (root / "research_tool" / "__init__.py").is_file():
        return False
    try:
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    return metadata.get("project", {}).get("name") == "research-tool"


def execute_deployment(
    plan: DeploymentPlan,
    runner: Callable[[tuple[str, ...]], int],
    *,
    required_probe: Callable[[str], bool],
) -> DeploymentResult:
    """Execute required steps fail-fast, then verify every selected capability."""
    completed: tuple[str, ...] = ()
    for step in plan.steps:
        try:
            return_code = runner(step.command)
        except OSError:
            return DeploymentResult(False, completed, failed_step=step.id)
        if return_code != 0:
            return DeploymentResult(False, completed, failed_step=step.id)
        completed = (*completed, step.id)

    missing = tuple(
        capability
        for capability in sorted(plan.profile.capabilities)
        if not required_probe(capability)
    )
    return DeploymentResult(not missing, completed, missing_capabilities=missing)


__all__ = [
    "FULL_CAPABILITIES",
    "CAPABILITY_FIXES",
    "PROFILES",
    "DeploymentPlan",
    "DeploymentResult",
    "InstallStep",
    "ProfileSpec",
    "build_deployment_plan",
    "execute_deployment",
    "provider_env_updates",
]
