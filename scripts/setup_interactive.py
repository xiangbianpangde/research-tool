#!/usr/bin/env python3
"""research-tool 交互式一键部署 / 配置向导。

用法::

    conda activate research-tool
    cd /Users/xbpd/Projects/research-tool
    research setup
    research setup --secrets-only
    research setup --tavily-only
    research setup --github-only
    python scripts/setup_interactive.py

密钥本地隐藏输入；切勿粘贴到聊天。写入项目根 .env（已 gitignore）。
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import warnings
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _is_source_root(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file() or not (path / "research_tool" / "__init__.py").is_file():
        return False
    try:
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    return metadata.get("project", {}).get("name") == "research-tool"


def _discover_root(
    script_path: Path | None = None,
    home: Path | None = None,
) -> Path:
    script = Path(__file__).resolve() if script_path is None else script_path.resolve()
    source_root = next((path for path in script.parents if _is_source_root(path)), None)
    if source_root is not None:
        return source_root
    configured = os.environ.get("RESEARCH_HOME")
    return Path(configured).expanduser() if configured else (home or Path.home()) / ".research"


ROOT = _discover_root()
if _is_source_root(ROOT) and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tool.presentation.setup_deployment import (  # noqa: E402
    CAPABILITY_FIXES,
    PROFILES as DEPLOYMENT_PROFILES,
    build_deployment_plan,
    execute_deployment,
    provider_env_updates,
)

ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
VENV = ROOT / ".venv"
RECEIPT_PATH = ROOT / ".research-deployment.json"
SOURCE_MODE = _is_source_root(ROOT)

PROFILES = {
    "1": {
        "name": DEPLOYMENT_PROFILES["minimal"].name,
        "desc": DEPLOYMENT_PROFILES["minimal"].description,
        "profile_key": "minimal",
        "extras": ",".join(DEPLOYMENT_PROFILES["minimal"].extras),
        "need_github": False,
        "need_proxy_ask": True,
        "need_tavily": False,
        "need_video": False,
        "need_x": False,
    },
    "2": {
        "profile_key": "recommended",
        "name": DEPLOYMENT_PROFILES["recommended"].name,
        "desc": DEPLOYMENT_PROFILES["recommended"].description,
        "extras": ",".join(DEPLOYMENT_PROFILES["recommended"].extras),
        "need_github": True,
        "need_proxy_ask": True,
        "need_tavily": True,
        "need_video": False,
        "need_x": False,
    },
    "3": {
        "profile_key": "full",
        "name": DEPLOYMENT_PROFILES["full"].name,
        "desc": DEPLOYMENT_PROFILES["full"].description,
        "extras": ",".join(DEPLOYMENT_PROFILES["full"].extras),
        "need_github": True,
        "need_proxy_ask": True,
        "need_tavily": True,
        "need_video": True,
        "need_x": True,
    },
}

LLM_PROVIDERS = {
    "1": ("deepseek", "DEEPSEEK_API_KEY", "DeepSeek"),
    "2": ("openai", "OPENAI_API_KEY", "OpenAI 兼容"),
    "3": ("anthropic", "ANTHROPIC_API_KEY", "Anthropic"),
    "4": ("minimax", "MINIMAX_API_KEY", "MiniMax"),
}

# 全部支持的密钥/配置项（写入 .env 的顺序）
ENV_ORDER = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_OPENAI_BASE_URL",
    "GITHUB_TOKEN",
    "TAVILY_API_KEY",
    "S2_API_KEY",
    "OPENALEX_MAILTO",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "YTDLP_PROXY",
    "GROQ_API_KEY",
    "TWITTER_AUTH_TOKEN",
    "TWITTER_CT0",
]

# 可选密钥目录：id → (env_key, title, help, secret?, default_ask_y)
OPTIONAL_KEYS: list[tuple[str, str, str, str, bool, bool]] = [
    (
        "github",
        "GITHUB_TOKEN",
        "GitHub 仓库搜索 / code search 配额",
        "Classic PAT：https://github.com/settings/tokens  可不勾任何 scope（公开足够）",
        True,
        True,
    ),
    (
        "tavily",
        "TAVILY_API_KEY",
        "Tavily 网页搜索（-s tavily）",
        "申请: https://app.tavily.com  → API Keys  形如 tvly-…",
        True,
        True,
    ),
    (
        "s2",
        "S2_API_KEY",
        "Semantic Scholar 配额（-s semantic_scholar，可选）",
        "https://www.semanticscholar.org/product/api  无 key 也能用，有 key 更稳",
        True,
        False,
    ),
    (
        "openalex",
        "OPENALEX_MAILTO",
        "OpenAlex / Crossref polite pool 邮箱",
        "填你的邮箱字符串（不是 token），提高限流友好度",
        False,
        True,
    ),
    (
        "proxy",
        "HTTPS_PROXY",
        "HTTP(S) 代理（国内访问 DDG 等）",
        "海外直连请跳过/清空；勿填未启动的 127.0.0.1:10809",
        False,
        False,
    ),
    (
        "ytdlp_proxy",
        "YTDLP_PROXY",
        "仅 yt-dlp/YouTube 用的代理（可选）",
        "一般可留空；与 HTTPS_PROXY 分开，避免误伤直连源",
        False,
        False,
    ),
    (
        "minimax_video",
        "MINIMAX_API_KEY",
        "MiniMax 视频理解/默认视频总结（完整档必需）",
        "与 Anthropic 厂商密钥隔离；只填写 MiniMax 自己的 token",
        True,
        False,
    ),
    (
        "groq",
        "GROQ_API_KEY",
        "Groq 视频转写 fallback（默认已用 MiniMax-M3）",
        "https://console.groq.com  可选；主路径是 MiniMax 多模态讲稿",
        True,
        False,
    ),
    (
        "twitter_auth",
        "TWITTER_AUTH_TOKEN",
        "X cookie auth_token（仅 twitter-cli）",
        "默认 x_backend=opencli：cookie 由 opencli 从浏览器自动取，无需手填。"
        "仅当改用 --x-backend twitter-cli 时才需要",
        True,
        False,
    ),
    (
        "twitter_ct0",
        "TWITTER_CT0",
        "X cookie ct0（仅 twitter-cli，与 auth_token 成对）",
        "opencli 模式跳过此项；见 opencli doctor",
        True,
        False,
    ),
]


def _banner() -> None:
    print(
        """
╔══════════════════════════════════════════════════════════╗
║         research-tool  交互式部署向导                    ║
║  档位/密钥 → 隐藏输入 → 写入 .env → preflight 自检       ║
╚══════════════════════════════════════════════════════════╝
"""
    )
    print(f"项目根: {ROOT}")
    print()


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw if raw else default


def _ask_yes(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(f"{prompt} ({d}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是", "1")


def _ask_secret(prompt: str) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            val = getpass.getpass(f"{prompt}（不回显，回车跳过）: ")
    except (Exception, getpass.GetPassWarning):
        print("[ERROR] 当前终端无法安全隐藏输入；已跳过。请换真实 TTY 后重试。")
        return ""
    return (val or "").strip()


def _load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def _write_env(path: Path, updates: dict[str, str], keep_existing: bool = True) -> None:
    for key, value in updates.items():
        if any(char in value for char in ("\n", "\r", "\0")):
            raise ValueError(f"{key} 含换行或 NUL，拒绝写入 .env")

    original = path.read_text(encoding="utf-8") if keep_existing and path.exists() else ""
    lines = original.splitlines() if original else [
        "# Generated/updated by scripts/setup_interactive.py",
        "# Do not commit. research setup 可再次更新。",
        "",
    ]
    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key = line.partition("=")[0].strip()
        if key in updates:
            if key not in seen:
                rendered.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        rendered.append(line)

    ordered_new_keys = [key for key in ENV_ORDER if key in updates and key not in seen]
    ordered_new_keys.extend(
        sorted(key for key in updates if key not in seen and key not in ENV_ORDER)
    )
    rendered.extend(f"{key}={updates[key]}" for key in ordered_new_keys)
    text = "\n".join(rendered).rstrip("\n") + "\n"
    if text == original:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as tmp:
            tmp.write(text)
            tmp_name = tmp.name
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if tmp_name and Path(tmp_name).exists():
            Path(tmp_name).unlink()


def _write_receipt(
    path: Path,
    profile_key: str,
    *,
    verified: bool = True,
    failure: str | None = None,
) -> None:
    """Persist a deterministic, secret-free record of verified capabilities."""
    profile = DEPLOYMENT_PROFILES[profile_key]
    version_probe = _run_probe([_python(), "--version"])
    target_python = (
        ((version_probe.stdout or "") + (version_probe.stderr or "")).strip()
        if version_probe and version_probe.returncode == 0
        else str(_python())
    )
    payload = {
        "schema": 1,
        "profile": profile_key,
        "capabilities": sorted(profile.capabilities),
        "python": target_python,
        "verified": verified,
    }
    if failure:
        payload["last_failure"] = failure
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as tmp:
            tmp.write(text)
            tmp_name = tmp.name
        os.replace(tmp_name, path)
    finally:
        if tmp_name and Path(tmp_name).exists():
            Path(tmp_name).unlink()


def _read_receipt_profile(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    profile = payload.get("profile") if isinstance(payload, dict) else None
    return profile if profile in DEPLOYMENT_PROFILES else None


def _option_value(argv: list[str], name: str) -> str | None:
    prefix = f"{name}="
    for index, value in enumerate(argv):
        if value.startswith(prefix):
            return value[len(prefix) :]
        if value == name and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _status(existing: dict[str, str], key: str) -> str:
    v = existing.get(key) or ""
    if not v:
        return "未配置"
    if "PROXY" in key:
        try:
            parsed = urlsplit(v)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return "已有=***"
        if not parsed.scheme or not host:
            return "已有=***"
        display_host = f"[{host}]" if ":" in host else host
        netloc = f"{display_host}:{port}" if port is not None else display_host
        return f"已有={urlunsplit((parsed.scheme, netloc, '', '', ''))}"
    if key in ("OPENALEX_MAILTO",):
        show = v if len(v) < 40 else v[:20] + "…"
        return f"已有={show}"
    return f"已有 len={len(v)}"


def _python() -> str:
    if not SOURCE_MODE:
        return sys.executable
    if sys.platform == "win32":
        p = VENV / "Scripts" / "python.exe"
    else:
        p = VENV / "bin" / "python"
    if p.exists():
        return str(p)
    return sys.executable


def _run(cmd: list[str] | tuple[str, ...]) -> int:
    print("  $", " ".join(cmd))
    return subprocess.call(  # noqa: S603 - argv comes from trusted plan
        cmd,
        cwd=str(ROOT),
        env=_safe_subprocess_env(allow_network=True, allow_pip=True),
    )


def _ensure_pip(python: str) -> None:
    """Bootstrap pip in uv-created virtual environments that omit it by default."""
    if _run([python, "-m", "pip", "--version"]) == 0:
        return
    print("[setup] 当前虚拟环境缺少 pip，使用 ensurepip 补齐 …")
    if _run([python, "-m", "ensurepip", "--upgrade"]) != 0:
        print("[ERROR] 无法为当前 Python 安装 pip")
        raise SystemExit(1)
    if _run([python, "-m", "pip", "--version"]) != 0:
        print("[ERROR] pip 引导完成后仍不可用")
        raise SystemExit(1)


def ensure_venv() -> None:
    if not SOURCE_MODE:
        print(f"[ok] wheel 安装模式，复用当前解释器: {sys.executable}")
        _ensure_pip(sys.executable)
        return
    py_win = VENV / "Scripts" / "python.exe"
    py_unix = VENV / "bin" / "python"
    if py_win.exists() or py_unix.exists():
        print("[ok] 已有 .venv")
    else:
        print("[setup] 创建 .venv …")
        if _run([sys.executable, "-m", "venv", str(VENV)]) != 0:
            print("[ERROR] venv 失败")
            raise SystemExit(1)
    _ensure_pip(_python())


def install_deps(profile_key: str) -> None:
    plan = build_deployment_plan(profile_key, python=_python(), project_root=ROOT)
    result = execute_deployment(plan, _run, required_probe=lambda _capability: True)
    if not result.ok:
        print(f"[ERROR] 安装步骤失败: {result.failed_step}")
        raise SystemExit(1)


def print_github_help() -> None:
    print(
        """
  GitHub Token:
    https://github.com/settings/tokens → Classic
    权限：可不勾任何 scope（公开搜索+配额+公开 code search）
    仅私有仓才勾 repo；禁止 admin/workflow/delete
    曾泄露过的 token 请先 Revoke 再新建
"""
    )


def prompt_one_key(
    updates: dict[str, str],
    existing: dict[str, str],
    env_key: str,
    title: str,
    help_text: str,
    *,
    secret: bool,
    force: bool = False,
    default_yes: bool = True,
) -> None:
    print(f"\n── {title}")
    print(f"  变量: {env_key}  |  {_status(existing, env_key)}")
    if help_text:
        for line in help_text.strip().splitlines():
            print(f"  {line}")
    if env_key == "GITHUB_TOKEN":
        print_github_help()
    if not force and not _ask_yes(f"配置/更新 {env_key}？", default_yes):
        print("  跳过")
        return
    if secret:
        val = _ask_secret(f"粘贴 {env_key}")
    else:
        val = _ask(f"输入 {env_key}", existing.get(env_key, ""))
    if val != "" or (not secret and val == "" and force):
        # 代理允许写空以清空
        if val or env_key in ("HTTPS_PROXY", "HTTP_PROXY", "YTDLP_PROXY"):
            if env_key == "HTTPS_PROXY":
                updates["HTTPS_PROXY"] = val
                updates["HTTP_PROXY"] = val
                print("  → HTTPS_PROXY/HTTP_PROXY 已记入")
            else:
                updates[env_key] = val
                if secret or "PROXY" in env_key:
                    print(f"  → {env_key} 已记入（不显示内容）")
                else:
                    print(f"  → {env_key}={val!r}")


def collect_llm(updates: dict[str, str], existing: dict[str, str]) -> str:
    print("\n【A】LLM（写报告/知识树用；不是 Tavily）")
    print("  0) 跳过  ← 已配过可直接回车")
    for k, (_, _, label) in LLM_PROVIDERS.items():
        ek = LLM_PROVIDERS[k][1]
        print(f"  {k}) {label}  [{_status(existing, ek)}]")
    lp = _ask("选择 LLM", "0")
    if lp in ("0", ""):
        print("→ 跳过 LLM")
        return "deepseek"
    provider, env_key, label = LLM_PROVIDERS.get(lp) or LLM_PROVIDERS["1"]
    print(f"→ {label}（{env_key}）")
    secret = _ask_secret(f"粘贴 {env_key}")
    if secret:
        updates.update(provider_env_updates(provider, secret))
    return provider


def collect_optional_all(
    updates: dict[str, str],
    existing: dict[str, str],
    *,
    profile: dict | None,
) -> None:
    """按目录逐项询问所有搜索/代理/视频/X 密钥。"""
    print("\n【B】搜索 / 代码 / 学术相关密钥")
    print("  （每一项可 y 配置 / n 跳过；直接回车用括号内默认）\n")

    # 按 profile 调整 default_yes
    defaults = {
        "github": True,
        "tavily": True,
        "s2": False,
        "openalex": True,
        "proxy": False,
        "ytdlp_proxy": False,
        "minimax_video": False,
        "groq": False,
        "twitter_auth": False,
        "twitter_ct0": False,
    }
    if profile:
        if profile.get("need_github"):
            defaults["github"] = True
        if profile.get("need_tavily"):
            defaults["tavily"] = True
        if profile.get("need_video"):
            defaults["minimax_video"] = True
            defaults["groq"] = True
        if profile.get("need_x"):
            defaults["twitter_auth"] = True
            defaults["twitter_ct0"] = True
        if profile.get("need_proxy_ask"):
            defaults["proxy"] = False  # 仍默认 n，避免写死代理

    for kid, env_key, title, help_text, secret, _ in OPTIONAL_KEYS:
        # proxy 特殊：可选清空
        if kid == "proxy" and existing.get("HTTPS_PROXY"):
            print(f"\n── {title}  |  {_status(existing, env_key)}")
            if _ask_yes("清空已有 HTTPS_PROXY（海外直连推荐）？", False):
                updates["HTTPS_PROXY"] = ""
                updates["HTTP_PROXY"] = ""
                print("  → 已清空代理")
                continue
        prompt_one_key(
            updates,
            existing,
            env_key,
            title,
            help_text,
            secret=secret,
            force=False,
            default_yes=defaults.get(kid, False),
        )


def collect_pick(updates: dict[str, str], existing: dict[str, str]) -> str:
    """自选要更新的项。"""
    print("\n请选择要配置的项（可多选，逗号分隔，如 2,3,5）：")
    print("  1) LLM API Key")
    items = list(OPTIONAL_KEYS)
    for i, (kid, env_key, title, _, _, _) in enumerate(items, start=2):
        print(f"  {i}) {title}  ({env_key})  [{_status(existing, env_key)}]")
    print("  a) 全部可选密钥（不含 LLM）")
    raw = _ask("输入编号", "3")  # 默认 tavily 较常见
    provider = "deepseek"
    if raw.lower() == "a":
        collect_optional_all(updates, {**existing, **updates}, profile=PROFILES["2"])
        return provider
    nums = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit():
            nums.add(int(part))
    if 1 in nums:
        provider = collect_llm(updates, existing)
    for i, (kid, env_key, title, help_text, secret, _) in enumerate(items, start=2):
        if i in nums:
            if kid == "proxy" and existing.get("HTTPS_PROXY"):
                if _ask_yes("清空已有 HTTPS_PROXY？", False):
                    updates["HTTPS_PROXY"] = ""
                    updates["HTTP_PROXY"] = ""
                    continue
            prompt_one_key(
                updates,
                existing,
                env_key,
                title,
                help_text,
                secret=secret,
                force=True,
                default_yes=True,
            )
    return provider


def _module_available(python: str, module: str) -> bool:
    result = subprocess.run(  # noqa: S603 - fixed interpreter/module probe
        [python, "-I", "-c", f"import {module}"],
        cwd=str(ROOT),
        env=_safe_subprocess_env(),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


_SAFE_HOST_ENV = frozenset(
    {
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "PATH",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
)
_NETWORK_ENV = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
_PIP_ENV = frozenset({"PIP_CONFIG_FILE", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"})


def _safe_subprocess_env(
    business_env: dict[str, str] | None = None,
    *,
    allow_network: bool = False,
    allow_pip: bool = False,
) -> dict[str, str]:
    safe = {key: value for key, value in os.environ.items() if key in _SAFE_HOST_ENV}
    if allow_network:
        for key in _NETWORK_ENV:
            value = (business_env or {}).get(key, os.environ.get(key))
            if value is not None:
                safe[key] = value
    if allow_pip:
        safe.update({key: value for key, value in os.environ.items() if key in _PIP_ENV})
    safe["RESEARCH_DISABLE_DOTENV"] = "1"
    if business_env is not None:
        safe.update({key: value for key, value in business_env.items() if key in ENV_ORDER})
    return safe


def _run_probe(
    command: list[str], *, timeout: float = 30, allow_network: bool = False
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603 - resolved executable, shell disabled
            command,
            cwd=str(ROOT),
            env=_safe_subprocess_env(allow_network=allow_network),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _capability_status(capability: str, env: dict[str, str]) -> tuple[bool, str]:
    py = _python()
    if capability == "core":
        return _module_available(py, "research_tool"), "import research_tool"
    if capability == "llm":
        provider = env.get("LLM_PROVIDER") or os.environ.get("LLM_PROVIDER") or "deepseek"
        key_env = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }.get(provider)
        key_ok = bool(key_env and (env.get(key_env) or os.environ.get(key_env)))
        sdk_ok = _module_available(py, "openai") and _module_available(py, "anthropic")
        return key_ok and sdk_ok, f"provider={provider}; key/sdk"
    module_groups = {
        "search": ("ddgs", "tavily"),
        "ui": ("gradio",),
        "video": ("yt_dlp", "faster_whisper"),
        "pdf": ("mineru",),
        "crawl": ("crawl4ai",),
    }
    if capability in module_groups:
        modules = module_groups[capability]
        modules_ok = all(_module_available(py, module) for module in modules)
        if capability == "video":
            minimax_ok = bool(env.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_API_KEY"))
            return modules_ok and minimax_ok, ", ".join((*modules, "MINIMAX_API_KEY"))
        return modules_ok, ", ".join(modules)
    if capability == "ffmpeg":
        command = shutil.which("ffmpeg")
        result = _run_probe([command, "-version"]) if command else None
        output = ((result.stdout or "") + (result.stderr or "")) if result else ""
        match = re.search(r"ffmpeg version\s+(\d+)", output, re.IGNORECASE)
        ok = bool(result and result.returncode == 0 and match and int(match.group(1)) >= 6)
        return ok, "ffmpeg >= 6"
    if capability == "deno":
        command = shutil.which("deno")
        result = _run_probe([command, "--version"]) if command else None
        match = re.search(r"^deno\s+(\d+)", result.stdout if result else "", re.IGNORECASE)
        ok = bool(result and result.returncode == 0 and match and int(match.group(1)) >= 2)
        return ok, "deno >= 2"
    if capability == "opencli":
        command = shutil.which("opencli")
        result = (
            _run_probe([command, "doctor"], timeout=40, allow_network=True)
            if command
            else None
        )
        return bool(result and result.returncode == 0), "opencli doctor"
    if capability == "crawl_browser":
        name = "crawl4ai-doctor.exe" if sys.platform == "win32" else "crawl4ai-doctor"
        local = Path(py).with_name(name)
        command = str(local) if local.exists() else shutil.which("crawl4ai-doctor")
        result = _run_probe([command], timeout=60) if command else None
        return bool(result and result.returncode == 0), "crawl4ai-doctor"
    return False, "无检测器"


def _llm_healthcheck(env: dict[str, str]) -> tuple[bool, str]:
    """Run a short PONG request in the target interpreter without echoing errors/secrets."""
    code = (
        "import asyncio; "
        "from research_tool.domain.config import load_config; "
        "from research_tool.infrastructure.llm.base import LLMClient; "
        "client=LLMClient.from_config(load_config().llm); "
        "asyncio.run(client.healthcheck(timeout_sec=15))"
    )
    provider = env.get("LLM_PROVIDER") or os.environ.get("LLM_PROVIDER") or "deepseek"
    key_env = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }.get(provider)
    selected_keys = ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", key_env)
    selected_env = {}
    for key in selected_keys:
        if not key:
            continue
        value = env[key] if key in env else os.environ.get(key)
        if value is not None:
            selected_env[key] = value
    try:
        result = subprocess.run(  # noqa: S603 - fixed isolated Python argv
            [_python(), "-I", "-c", code],
            cwd=str(ROOT),
            env=_safe_subprocess_env(selected_env, allow_network=True),
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "鉴权测试无法执行或超时"
    if result.returncode == 0:
        return True, "PONG（provider/key/endpoint 已验证）"
    return False, "PONG 失败；请检查 provider/model/base_url/key（详细错误已隐藏）"


def _minimax_healthcheck(env: dict[str, str]) -> tuple[bool, str]:
    """Verify the separate MiniMax credential required by the default video path."""
    code = (
        "import asyncio; "
        "from research_tool.infrastructure.ingest.transcriber import "
        "resolve_minimax_api_key, resolve_minimax_base_url; "
        "from research_tool.infrastructure.llm.base import LLMClient; "
        "client=LLMClient.create(provider='minimax', api_key=resolve_minimax_api_key(), "
        "model='MiniMax-M3', base_url=resolve_minimax_base_url()); "
        "asyncio.run(client.healthcheck(timeout_sec=15))"
    )
    selected_env = {}
    for key in ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_OPENAI_BASE_URL"):
        value = env[key] if key in env else os.environ.get(key)
        if value is not None:
            selected_env[key] = value
    try:
        result = subprocess.run(  # noqa: S603 - fixed isolated Python argv
            [_python(), "-I", "-c", code],
            cwd=str(ROOT),
            env=_safe_subprocess_env(selected_env, allow_network=True),
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "MiniMax 视频鉴权测试无法执行或超时"
    if result.returncode == 0:
        return True, "PONG（MiniMax 视频 key/endpoint 已验证）"
    return False, "MiniMax PONG 失败；视频链路不会标记完成（详细错误已隐藏）"


def preflight(env: dict[str, str], profile_key: str | None = None) -> bool:
    print("\n========== 自检 preflight ==========")
    py = _python()
    checks: list[tuple[str, bool, str]] = []

    core_ok = _module_available(py, "research_tool")
    checks.append(("research_tool import", core_ok, "OK" if core_ok else "导入失败（详情已隐藏）"))

    def has(*keys: str) -> bool:
        return any(bool(env.get(k) or os.environ.get(k)) for k in keys)

    checks.append(
        (
            "LLM Key",
            has(
                "DEEPSEEK_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "MINIMAX_API_KEY",
            ),
            (
                "缺则无法 report"
                if not has(
                    "DEEPSEEK_API_KEY",
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "MINIMAX_API_KEY",
                )
                else "OK"
            ),
        )
    )
    for key, label in [
        ("GITHUB_TOKEN", "GitHub"),
        ("TAVILY_API_KEY", "Tavily"),
        ("S2_API_KEY", "Semantic Scholar"),
        ("OPENALEX_MAILTO", "OpenAlex 邮箱"),
        ("GROQ_API_KEY", "Groq"),
        ("TWITTER_AUTH_TOKEN", "X auth_token"),
        ("TWITTER_CT0", "X ct0"),
    ]:
        v = env.get(key) or os.environ.get(key) or ""
        ok = bool(v)
        detail = f"len={len(v)}" if ok and "MAILTO" not in key and "PROXY" not in key else (
            v[:30] if ok and ("MAILTO" in key or "PROXY" in key) else "未配置"
        )
        checks.append((label, ok, detail))

    proxy = env.get("HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or ""
    if proxy and "127.0.0.1" in proxy:
        import socket

        m = re.search(r":(\d+)", proxy)
        up = False
        if m:
            try:
                s = socket.create_connection(("127.0.0.1", int(m.group(1))), timeout=0.4)
                s.close()
                up = True
            except OSError:
                up = False
        safe_proxy = _status({"HTTPS_PROXY": proxy}, "HTTPS_PROXY").removeprefix("已有=")
        checks.append(("本地代理进程", up, safe_proxy + (" 在听" if up else " 未监听")))

    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '·'} {name}: {detail}")

    required_ok = True
    if profile_key:
        profile = DEPLOYMENT_PROFILES[profile_key]
        print(f"\n  档位验收: {profile.name}")
        for capability in sorted(profile.capabilities):
            ok, detail = _capability_status(capability, env)
            required_ok = required_ok and ok
            print(f"  {'✓' if ok else '✗'} {capability}: {detail}")
            if not ok:
                print(f"      修复: {CAPABILITY_FIXES[capability]}")
        if "llm" in profile.capabilities:
            llm_ok, detail = _llm_healthcheck(env)
            required_ok = required_ok and llm_ok
            print(f"  {'✓' if llm_ok else '✗'} llm_pong: {detail}")
        if "video" in profile.capabilities:
            video_auth_ok, detail = _minimax_healthcheck(env)
            required_ok = required_ok and video_auth_ok
            print(f"  {'✓' if video_auth_ok else '✗'} minimax_video_pong: {detail}")
    else:
        print("  （· = 未配置，仅在用到对应功能时需要）")
    print("====================================\n")
    return required_ok


def _finish(
    updates: dict[str, str],
    provider: str,
    *,
    profile_key: str | None = None,
) -> None:
    if updates or not ENV_PATH.exists():
        _write_env(ENV_PATH, updates, keep_existing=True)
        print(f"\n[ok] 已写入 {ENV_PATH}")
        for k in updates:
            if updates[k] != "" or "PROXY" in k:
                print(f"  · 更新了 {k}")
    else:
        print("\n[skip] 无变更")

    print(
        f"""
[提示]
  config.yaml 使用 ${{ENV}} 引用，一般无需手改。
  LLM provider 当前倾向: {provider}
  使用示例:
    research collect "主题" -s github -s openalex
    research collect "主题" -s tavily
    research collect "主题" -s semantic_scholar
"""
    )
    final_env = {**_load_env_file(ENV_PATH), **updates}
    verified = preflight(final_env, profile_key)
    if profile_key and not verified:
        print("[ERROR] 部署未完成：所选档位仍有必需能力缺失；请按 ✗ 项补齐后重跑。")
        raise SystemExit(1)
    if profile_key:
        _write_receipt(RECEIPT_PATH, profile_key)
        print(f"[ok] 已写入无密钥部署回执: {RECEIPT_PATH}")
    print("部署完成。再次配置: research setup")
    print("  只改 Tavily: research setup --tavily-only")
    print("  只改 GitHub: research setup --github-only")
    print("  自选多项:   research setup  → 选 5")


def _run_check_only(requested_profile: str | None) -> None:
    profile_key = requested_profile or _read_receipt_profile(RECEIPT_PATH)
    if profile_key not in DEPLOYMENT_PROFILES:
        print("[ERROR] 无可用部署档位；请传 --profile minimal|recommended|full")
        raise SystemExit(1)
    verified = preflight(_load_env_file(ENV_PATH), profile_key)
    if not verified:
        _write_receipt(
            RECEIPT_PATH,
            profile_key,
            verified=False,
            failure="preflight_failed",
        )
        print("[ERROR] 部署验收失败；已保留目标档位，请运行 research setup 修复。")
        raise SystemExit(1)
    _write_receipt(RECEIPT_PATH, profile_key)
    print(f"[ok] {profile_key} 档位仍完整，无需重装。")


def _run_full_deployment(
    updates: dict[str, str],
    existing: dict[str, str],
    requested_profile: str | None = None,
) -> None:
    print("选择部署档位：")
    for key, item in PROFILES.items():
        print(f"  {key}) {item['name']} — {item['desc']}")
    previous_profile = _read_receipt_profile(RECEIPT_PATH)
    default_choice = {"minimal": "1", "recommended": "2", "full": "3"}.get(
        previous_profile, "3"
    )
    profile_choice = {
        "minimal": "1",
        "recommended": "2",
        "full": "3",
    }.get(requested_profile)
    choice = profile_choice or _ask("输入数字", default_choice)
    profile = PROFILES.get(choice) or PROFILES["2"]
    if _ask_yes("创建 venv 并安装依赖？", True):
        ensure_venv()
        install_deps(profile["profile_key"])
    provider = collect_llm(updates, existing)
    collect_optional_all(updates, {**existing, **updates}, profile=profile)
    _finish(updates, provider, profile_key=profile["profile_key"])


def _validate_profile_combination(
    requested_profile: str | None,
    *,
    secrets_only: bool,
    github_only: bool,
    tavily_only: bool,
) -> None:
    if requested_profile and requested_profile not in DEPLOYMENT_PROFILES:
        print("[ERROR] 无效档位；请传 --profile minimal|recommended|full")
        raise SystemExit(2)
    if requested_profile and (secrets_only or github_only or tavily_only):
        print("[ERROR] --profile 不能与密钥专用参数组合")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 11):
        print("[ERROR] research-tool 需要 Python 3.11+")
        raise SystemExit(1)
    secrets_only = "--secrets-only" in argv or "-s" in argv
    github_only = "--github-only" in argv or "-g" in argv
    tavily_only = "--tavily-only" in argv or "-t" in argv
    requested_profile = _option_value(argv, "--profile")

    _banner()
    if "--check-only" in argv:
        _run_check_only(requested_profile)
        return
    _validate_profile_combination(
        requested_profile,
        secrets_only=secrets_only,
        github_only=github_only,
        tavily_only=tavily_only,
    )

    existing = _load_env_file(ENV_PATH)
    updates: dict[str, str] = {}
    provider = "deepseek"

    if requested_profile:
        _run_full_deployment(updates, existing, requested_profile)
        return

    if tavily_only:
        prompt_one_key(
            updates,
            existing,
            "TAVILY_API_KEY",
            "Tavily 网页搜索",
            "https://app.tavily.com  tvly-…",
            secret=True,
            force=True,
        )
        _finish(updates, provider)
        return

    if github_only:
        prompt_one_key(
            updates,
            existing,
            "GITHUB_TOKEN",
            "GitHub Token",
            "Classic PAT 可不勾 scope",
            secret=True,
            force=True,
        )
        _finish(updates, provider)
        return

    if secrets_only:
        mode = "2"
    else:
        print("请选择操作：")
        print("  1) 完整部署（装依赖 + 全部密钥向导）")
        print("  2) 更新密钥（逐步问 LLM/GitHub/Tavily/S2/代理/Groq/X…）")
        print("  3) 只更新 GITHUB_TOKEN")
        print("  4) 只更新 TAVILY_API_KEY")
        print("  5) 自选要更新的密钥（多选编号）")
        mode = _ask("输入数字", "1")

    if mode == "3":
        prompt_one_key(
            updates,
            existing,
            "GITHUB_TOKEN",
            "GitHub Token",
            "Classic PAT 可不勾 scope",
            secret=True,
            force=True,
        )
        _finish(updates, provider)
        return

    if mode == "4":
        prompt_one_key(
            updates,
            existing,
            "TAVILY_API_KEY",
            "Tavily",
            "https://app.tavily.com",
            secret=True,
            force=True,
        )
        _finish(updates, provider)
        return

    if mode == "5":
        provider = collect_pick(updates, existing)
        _finish(updates, provider)
        return

    if mode == "2" or secrets_only:
        provider = collect_llm(updates, existing)
        collect_optional_all(updates, {**existing, **updates}, profile=PROFILES["2"])
        _finish(updates, provider)
        return

    _run_full_deployment(updates, existing)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
