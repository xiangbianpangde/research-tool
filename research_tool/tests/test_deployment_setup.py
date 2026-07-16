"""Deployment completeness contract for the packaged interactive setup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_interactive as wizard

from research_tool.presentation.setup_deployment import (
    CAPABILITY_FIXES,
    FULL_CAPABILITIES,
    PROFILES,
    build_deployment_plan,
    execute_deployment,
    provider_env_updates,
)
from research_tool.domain import config as config_module
from research_tool.infrastructure.ingest.transcriber import resolve_minimax_api_key


def test_full_profile_accounts_for_every_supported_capability():
    full = PROFILES["full"]

    assert full.capabilities == FULL_CAPABILITIES
    assert set(full.extras) == {"llm", "search", "ui", "video", "pdf", "crawl"}
    assert {"ffmpeg", "deno", "opencli"} <= set(full.required_commands)
    assert FULL_CAPABILITIES <= CAPABILITY_FIXES.keys()


@pytest.mark.parametrize(
    ("provider", "secret_key", "model", "base_url"),
    [
        ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat", "https://api.deepseek.com/v1"),
        ("openai", "OPENAI_API_KEY", "gpt-4o-mini", ""),
        ("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-4-6", ""),
        ("minimax", "MINIMAX_API_KEY", "MiniMax-M3", "https://api.minimaxi.com/v1"),
    ],
)
def test_provider_selection_persists_complete_runtime_identity(
    provider, secret_key, model, base_url
):
    updates = provider_env_updates(provider, "synthetic-secret")

    assert updates["LLM_PROVIDER"] == provider
    assert updates["LLM_MODEL"] == model
    assert updates["LLM_BASE_URL"] == base_url
    assert updates[secret_key] == "synthetic-secret"


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_any_required_install_failure_fails_deployment(failed_step, tmp_path):
    plan = build_deployment_plan("full", python="python", project_root=tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 9 if len(calls) - 1 == failed_step else 0

    result = execute_deployment(plan, runner, required_probe=lambda _name: True)

    assert result.ok is False
    assert result.failed_step == plan.steps[failed_step].id
    assert len(calls) == failed_step + 1


def test_missing_required_postflight_capability_is_not_success(tmp_path):
    plan = build_deployment_plan("full", python="python", project_root=tmp_path)

    result = execute_deployment(
        plan,
        lambda _command: 0,
        required_probe=lambda name: name != "ffmpeg",
    )

    assert result.ok is False
    assert result.missing_capabilities == ("ffmpeg",)


def test_wheel_contains_packaged_setup_module():
    wheels = sorted(Path("dist").glob("research_tool-*.whl"))
    if not wheels:
        pytest.skip("wheel is built by the packaging verification step")

    with zipfile.ZipFile(wheels[-1]) as archive:
        assert "research_tool/presentation/setup_deployment.py" in archive.namelist()
        assert "research_tool/presentation/setup_interactive.py" in archive.namelist()


def test_interactive_provider_choice_writes_identity_not_cross_vendor_key(monkeypatch):
    monkeypatch.setattr(wizard, "_ask", lambda *_args, **_kwargs: "4")
    monkeypatch.setattr(wizard, "_ask_secret", lambda *_args, **_kwargs: "minimax-secret")
    updates: dict[str, str] = {}

    provider = wizard.collect_llm(updates, {})

    assert provider == "minimax"
    assert updates["LLM_PROVIDER"] == "minimax"
    assert updates["LLM_MODEL"] == "MiniMax-M3"
    assert updates["MINIMAX_API_KEY"] == "minimax-secret"
    assert "ANTHROPIC_API_KEY" not in updates


def test_profile_preflight_fails_when_any_selected_capability_is_missing(monkeypatch):
    monkeypatch.setattr(
        wizard,
        "_capability_status",
        lambda capability, _env: (capability != "pdf", "synthetic probe"),
    )
    monkeypatch.setattr(
        wizard, "_llm_healthcheck", lambda _env: (True, "PONG"), raising=False
    )
    monkeypatch.setattr(
        wizard, "_minimax_healthcheck", lambda _env: (True, "PONG"), raising=False
    )

    assert wizard.preflight({}, "full") is False


def test_profile_preflight_requires_live_llm_authentication(monkeypatch):
    monkeypatch.setattr(
        wizard,
        "_capability_status",
        lambda _capability, _env: (True, "synthetic probe"),
    )
    monkeypatch.setattr(
        wizard,
        "_llm_healthcheck",
        lambda _env: (False, "鉴权失败"),
        raising=False,
    )

    assert wizard.preflight({}, "minimal") is False


def test_full_profile_requires_separate_minimax_video_authentication(monkeypatch):
    monkeypatch.setattr(
        wizard,
        "_capability_status",
        lambda _capability, _env: (True, "synthetic probe"),
    )
    monkeypatch.setattr(wizard, "_llm_healthcheck", lambda _env: (True, "PONG"))
    monkeypatch.setattr(
        wizard,
        "_minimax_healthcheck",
        lambda _env: (False, "MiniMax 鉴权失败"),
        raising=False,
    )

    assert wizard.preflight({"MINIMAX_API_KEY": "invalid"}, "full") is False


def test_setup_identity_clears_stale_yaml_endpoint(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n"
        "  provider: deepseek\n"
        "  model: deepseek-chat\n"
        "  base_url: https://api.deepseek.com/v1\n"
        "  api_key: stale-deepseek-key\n",
        encoding="utf-8",
    )
    updates = provider_env_updates("openai", "openai-secret")
    for key, value in updates.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    config = config_module.load_config(path)

    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o-mini"
    assert config.llm.base_url is None
    assert config.llm.api_key == os.environ["OPENAI_API_KEY"]


def test_env_update_is_comment_preserving_and_rejects_newline_injection(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# user note\nCUSTOM_FLAG=keep-me\nLLM_PROVIDER=deepseek\n",
        encoding="utf-8",
    )

    wizard._write_env(env_path, {"LLM_PROVIDER": "minimax", "MINIMAX_API_KEY": "safe"})

    rendered = env_path.read_text(encoding="utf-8")
    assert "# user note" in rendered
    assert "CUSTOM_FLAG=keep-me" in rendered
    assert "LLM_PROVIDER=minimax" in rendered
    with pytest.raises(ValueError, match="换行"):
        wizard._write_env(env_path, {"MINIMAX_API_KEY": "safe\nINJECTED=yes"})


def test_deployment_receipt_records_verified_capabilities_without_environment_secrets(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MINIMAX_API_KEY", "RECEIPT-MUST-NOT-CONTAIN-SECRET")
    receipt_path = tmp_path / ".research-deployment.json"

    wizard._write_receipt(receipt_path, "full")

    rendered = receipt_path.read_text(encoding="utf-8")
    assert "RECEIPT-MUST-NOT-CONTAIN-SECRET" not in rendered
    assert '"profile": "full"' in rendered
    for capability in FULL_CAPABILITIES:
        assert capability in rendered


def test_setup_status_redacts_proxy_userinfo():
    status = wizard._status(
        {"HTTPS_PROXY": "http://alice:proxy-password@127.0.0.1:1080/private?token=secret"},
        "HTTPS_PROXY",
    )

    assert status == "已有=http://127.0.0.1:1080"


def test_proxy_update_confirmation_never_echoes_credentials(monkeypatch, capsys):
    monkeypatch.setattr(
        wizard,
        "_ask",
        lambda *_args, **_kwargs: "http://alice:proxy-password@proxy.example:8080",
    )
    updates: dict[str, str] = {}

    wizard.prompt_one_key(
        updates,
        {},
        "YTDLP_PROXY",
        "Video proxy",
        "",
        secret=False,
        force=True,
    )

    output = capsys.readouterr().out
    assert "proxy-password" not in output
    assert "alice" not in output
    assert updates["YTDLP_PROXY"].endswith("@proxy.example:8080")


def test_check_only_revalidates_profile_without_prompt_or_install(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(wizard, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(wizard, "RECEIPT_PATH", tmp_path / "receipt.json")
    monkeypatch.setattr(wizard, "_banner", lambda: None)
    monkeypatch.setattr(wizard, "_ask", lambda *_args, **_kwargs: pytest.fail("prompted"))
    monkeypatch.setattr(wizard, "install_deps", lambda *_args: pytest.fail("installed"))
    monkeypatch.setattr(
        wizard,
        "preflight",
        lambda _env, profile: calls.append(profile) or True,
    )

    wizard.main(["--check-only", "--profile", "minimal"])

    assert calls == ["minimal"]
    assert wizard.RECEIPT_PATH.is_file()


def test_check_only_returns_nonzero_when_receipt_profile_is_incomplete(monkeypatch, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"profile":"full","verified":true}\n', encoding="utf-8")
    monkeypatch.setattr(wizard, "RECEIPT_PATH", receipt)
    monkeypatch.setattr(wizard, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(wizard, "_banner", lambda: None)
    monkeypatch.setattr(wizard, "preflight", lambda _env, _profile: False)

    with pytest.raises(SystemExit) as exc_info:
        wizard.main(["--check-only"])

    assert exc_info.value.code == 1
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["profile"] == "full"
    assert payload["verified"] is False
    assert payload["last_failure"] == "preflight_failed"


def test_profile_option_selects_deployment_without_profile_prompt(monkeypatch, tmp_path):
    calls: list[str | None] = []
    monkeypatch.setattr(wizard, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(wizard, "_banner", lambda: None)
    monkeypatch.setattr(
        wizard,
        "_run_full_deployment",
        lambda _updates, _existing, profile=None: calls.append(profile),
    )

    wizard.main(["--profile", "full"])

    assert calls == ["full"]


def test_first_interactive_deployment_defaults_to_full_profile(monkeypatch, tmp_path):
    defaults: list[str] = []
    completed: list[str | None] = []
    monkeypatch.setattr(wizard, "RECEIPT_PATH", tmp_path / "missing-receipt.json")
    monkeypatch.setattr(
        wizard,
        "_ask",
        lambda _prompt, default="": defaults.append(default) or default,
    )
    monkeypatch.setattr(wizard, "_ask_yes", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(wizard, "collect_llm", lambda *_args: "deepseek")
    monkeypatch.setattr(wizard, "collect_optional_all", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wizard,
        "_finish",
        lambda _updates, _provider, *, profile_key=None: completed.append(profile_key),
    )

    wizard._run_full_deployment({}, {})

    assert defaults == ["3"]
    assert completed == ["full"]


def test_existing_uv_venv_bootstraps_missing_pip(monkeypatch, tmp_path):
    venv = tmp_path / ".venv"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    calls: list[tuple[str, ...]] = []
    return_codes = iter((1, 0, 0))
    monkeypatch.setattr(wizard, "SOURCE_MODE", True)
    monkeypatch.setattr(wizard, "VENV", venv)
    monkeypatch.setattr(
        wizard,
        "_run",
        lambda command: calls.append(tuple(command)) or next(return_codes),
    )

    wizard.ensure_venv()

    assert calls == [
        (str(python), "-m", "pip", "--version"),
        (str(python), "-m", "ensurepip", "--upgrade"),
        (str(python), "-m", "pip", "--version"),
    ]


def test_wheel_root_ignores_unrelated_current_project(monkeypatch, tmp_path):
    unrelated = tmp_path / "other-project"
    unrelated.mkdir()
    (unrelated / "pyproject.toml").write_text(
        '[project]\nname = "malicious-project"\nversion = "1"\n', encoding="utf-8"
    )
    fake_script = tmp_path / "site-packages" / "research_tool" / "presentation" / "setup.py"
    home = tmp_path / "home"
    monkeypatch.chdir(unrelated)
    monkeypatch.delenv("RESEARCH_HOME", raising=False)

    assert wizard._discover_root(fake_script, home) == home / ".research"
    plan = build_deployment_plan("minimal", python="python", project_root=unrelated)
    package_step = next(step for step in plan.steps if step.id == "package")
    assert "-e" not in package_step.command
    assert package_step.command[-1] == "research-tool[llm,search]"


def test_llm_healthcheck_uses_isolated_python_and_env_allowlist(monkeypatch):
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("PYTHONPATH", "/tmp/untrusted")
    monkeypatch.setenv("PYTHONHOME", "/tmp/untrusted-home")
    monkeypatch.setenv("GITHUB_TOKEN", "github-must-not-reach-llm-probe")
    monkeypatch.setenv("LLM_BASE_URL", "https://stale-endpoint.invalid/v1")
    monkeypatch.setattr(wizard.subprocess, "run", fake_run)
    monkeypatch.setattr(wizard, "_python", lambda: "/safe/venv/bin/python")

    ok, _detail = wizard._llm_healthcheck(
        {
            "LLM_PROVIDER": "minimax",
            "LLM_BASE_URL": "",
            "MINIMAX_API_KEY": "synthetic-secret",
        }
    )

    assert ok is True
    assert captured["command"][:2] == ["/safe/venv/bin/python", "-I"]
    assert "from_config(load_config().llm)" in captured["command"][-1]
    assert "healthcheck(" in captured["command"][-1]
    assert "health_check(" not in captured["command"][-1]
    assert "PYTHONPATH" not in captured["env"]
    assert "PYTHONHOME" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert captured["env"]["RESEARCH_DISABLE_DOTENV"] == "1"
    assert captured["env"]["LLM_BASE_URL"] == ""
    assert captured["env"]["MINIMAX_API_KEY"] == "synthetic-secret"


def test_install_and_external_probe_environments_exclude_application_secrets(monkeypatch):
    captured: dict = {}

    def fake_call(_command, **kwargs):
        captured["env"] = kwargs["env"]
        return 0

    for key in ("MINIMAX_API_KEY", "GITHUB_TOKEN", "TWITTER_AUTH_TOKEN"):
        monkeypatch.setenv(key, f"{key}-must-not-leak")
    monkeypatch.setattr(wizard.subprocess, "call", fake_call)

    assert wizard._run(("python", "-m", "pip", "check")) == 0
    assert "MINIMAX_API_KEY" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "TWITTER_AUTH_TOKEN" not in captured["env"]
    assert "MINIMAX_API_KEY" not in wizard._safe_subprocess_env()


def test_full_video_requires_minimax_key_even_with_other_llm(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(wizard, "_module_available", lambda _python, _module: True)

    ok, detail = wizard._capability_status("video", {"LLM_PROVIDER": "openai"})

    assert ok is False
    assert "MINIMAX_API_KEY" in detail


def test_minimax_selector_never_falls_back_to_anthropic_vendor_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-must-not-cross-vendors")

    assert resolve_minimax_api_key() is None


@pytest.mark.parametrize(
    ("capability", "stdout", "returncode", "expected"),
    [
        ("ffmpeg", "ffmpeg version 5.1", 0, False),
        ("ffmpeg", "ffmpeg version 6.1", 0, True),
        ("deno", "deno 1.46.0", 0, False),
        ("deno", "deno 2.1.0", 0, True),
        ("opencli", "doctor failed", 1, False),
        ("opencli", "doctor ok", 0, True),
        ("crawl_browser", "browser missing", 1, False),
        ("crawl_browser", "browser ok", 0, True),
    ],
)
def test_external_tool_probe_requires_working_version_or_doctor(
    monkeypatch, capability, stdout, returncode, expected
):
    monkeypatch.setattr(wizard.shutil, "which", lambda command: f"/safe/bin/{command}")
    monkeypatch.setattr(
        wizard,
        "_run_probe",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        ),
    )

    ok, _detail = wizard._capability_status(capability, {})

    assert ok is expected


def test_runtime_loads_env_written_to_user_research_home(tmp_path):
    research_home = tmp_path / "research-home"
    research_home.mkdir()
    (research_home / ".env").write_text(
        "MINIMAX_API_KEY=user-home-synthetic-key\n", encoding="utf-8"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MINIMAX_API_KEY", "PYTHONPATH", "PYTHONHOME"}
    }
    env["RESEARCH_HOME"] = str(research_home)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, research_tool; print(os.environ.get('MINIMAX_API_KEY', ''))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "user-home-synthetic-key"
