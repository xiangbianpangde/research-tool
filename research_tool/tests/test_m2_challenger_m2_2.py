"""Challenger M2-2 Empirical Security & Adversarial Stress Suite.

Adversarial stress-testing targeting Milestone 2 (M2):
1. Feature F10: WebUI path safety (`is_safe_path`, `_assert_safe_output_dir`, `run_web` guards).
   - Directory traversal payloads (`../../etc/passwd`, nested escapes).
   - Null byte injection (`foo\x00bar`, leading, trailing, embedded).
   - Absolute escapes outside base dir (`/etc/passwd`, `/`, `/tmp`).
   - Symlink traps (pointing outside, pointing inside, broken symlinks, symlink loops).
   - Platform separator behavior (POSIX vs Windows backslash).
   - Zero Workspace Mutation hard guards protecting repository source, docs, tests, and root.
2. Feature F10 & F09: Strict mode enforcement (`RESEARCH_AGENT_STRICT=1`).
   - CLI blocking `--mode brief`, `--skip extract`, `--skip inspect`, `--skip qgate`.
   - WebUI `run_web` blocking `--mode brief`, `do_extract=False`.
   - Normalization and resilience under canonical aliases (`network` -> `knowledge`, `gate` -> `qgate`).
   - Truthiness discrepancy analysis between CLI and WebUI (`"1"` vs `"true"`/`"yes"`).
3. Feature F11: Config example template (`docs/config.example.yaml`).
   - Clean UTF-8, no non-printable or corrupt bytes.
   - Purged obsolete feature flags (`nine_loop`, `deepen`, `deepen_as_strategy`, `profile_iterations`).
   - Direct deserialization and compatibility with `load_config`.
   - Round-trip modification of all closed-loop sections (`inspect`, `targeted`, `qgate`, `budget_lease`).
   - Boundary validation stress and edge cases (`TargetedConfig`, `InspectConfig`, `BudgetLeaseConfig`).
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
from typing import Any
import pytest
from typer.testing import CliRunner
import yaml

from research_tool.domain.config import load_config
from research_tool.domain.errors import ConfigValidationError
from research_tool.domain.models import (
    BudgetLeaseConfig,
    InspectConfig,
    PipelineConfig,
    QGateConfig,
    TargetedConfig,
)
from research_tool.presentation.cli import app, _is_strict_agent
from research_tool.presentation.webui import (
    _assert_safe_output_dir,
    is_safe_path,
    run_web,
)


# ============================================================================
# Section 1: Path Traversal Payloads & Symlink Traps (Feature F10)
# ============================================================================


class TestWebUIPathSafetyAdversarial:
    """Adversarial stress-testing of is_safe_path and directory guards."""

    def test_directory_traversal_payloads_rejected(self, tmp_path: Path):
        """Verify is_safe_path rejects all directory traversal payloads escaping base."""
        base = tmp_path / "safe_base"
        base.mkdir()

        payloads = [
            "../../etc/passwd",
            "../other",
            "foo/../../bar",
            "sub/dir/../../../../etc/shadow",
            "sub/../../..",
            "nested/level1/level2/../../../../root_escape",
            "./../escape",
            "..",
            "../",
            "/",
            "/etc/passwd",
            "/tmp",
        ]
        for payload in payloads:
            assert is_safe_path(base, payload) is False, f"Failed to reject payload: {payload}"

    def test_safe_relative_paths_accepted(self, tmp_path: Path):
        """Verify is_safe_path accepts legitimately contained relative paths."""
        base = tmp_path / "safe_base"
        base.mkdir()

        safe_paths = [
            "sub",
            "sub/dir",
            "sub/nested/file.txt",
            "sub/../sub",  # Internal canonical normalization
            "sub/dir/../dir2",
            ".",
            "./child",
        ]
        for p in safe_paths:
            assert is_safe_path(base, p) is True, f"Legitimate path was rejected: {p}"

    def test_null_byte_injection_rejected(self, tmp_path: Path):
        """Verify is_safe_path strictly rejects strings with null bytes."""
        base = tmp_path / "safe_base"
        base.mkdir()

        null_payloads = [
            "foo\x00bar",
            "\x00",
            "safe_dir\x00/../../etc/passwd",
            "target.pdf\x00.exe",
            "\x00leading",
            "trailing\x00",
            "sub/\x00/sub2",
        ]
        for payload in null_payloads:
            assert is_safe_path(base, payload) is False, f"Failed to reject null byte payload: {repr(payload)}"

        # Null byte in base_dir itself
        assert is_safe_path(str(base) + "\x00", "sub") is False

    def test_symlink_traps_pointing_outside(self, tmp_path: Path):
        """Empirically test symlink traps attempting to escape base directory."""
        base = tmp_path / "sym_base"
        base.mkdir()
        outside = tmp_path / "outside_secret"
        outside.mkdir()
        secret_file = outside / "secret.txt"
        secret_file.write_text("classified data", encoding="utf-8")

        # 1. Symlink inside base pointing directly outside
        escape_link = base / "escape_link"
        try:
            escape_link.symlink_to(outside)
        except OSError:
            pytest.skip("OS does not permit symlink creation")

        assert is_safe_path(base, "escape_link") is False
        assert is_safe_path(base, "escape_link/secret.txt") is False

        # 2. Symlink inside pointing to an internal directory (safe)
        internal_target = base / "internal_dir"
        internal_target.mkdir()
        internal_link = base / "internal_link"
        internal_link.symlink_to(internal_target)

        assert is_safe_path(base, "internal_link") is True

        # 3. Broken symlink pointing outside base
        broken_link = base / "broken_link"
        broken_link.symlink_to(outside / "does_not_exist")

        assert is_safe_path(base, "broken_link") is False

        # 4. Broken symlink pointing inside base
        broken_in = base / "broken_in"
        broken_in.symlink_to(base / "does_not_exist")

        assert is_safe_path(base, "broken_in") is True

        # 5. Symlink loop / cycle
        cycle_a = base / "cycle_a"
        cycle_b = base / "cycle_b"
        cycle_a.symlink_to(cycle_b)
        cycle_b.symlink_to(cycle_a)

        assert is_safe_path(base, "cycle_a") is False

    def test_backslash_behavior_documented(self, tmp_path: Path):
        """Document platform behavior for backslash traversal (..\\..\\windows).

        On POSIX (Linux/macOS), backslash '\\' is a valid filename character and does NOT
        act as a path separator. Hence, Path(base) / '..\\..\\windows' resolves to
        a file inside base directory rather than escaping. On Windows, ntpath resolves
        '\\' as a separator and escapes.
        """
        base = tmp_path / "safe_base"
        base.mkdir()
        payload = "..\\..\\windows"
        res = is_safe_path(base, payload)
        if os.name == "nt":
            assert res is False
        else:
            # On POSIX, it is treated as a file literal inside base
            resolved = (base / payload).resolve()
            assert resolved.is_relative_to(base.resolve())
            assert res is True


class TestZeroWorkspaceMutationGuards:
    """Adversarial stress-testing of _assert_safe_output_dir and run_web input validation."""

    def test_assert_safe_output_dir_repo_protection(self):
        """Verify _assert_safe_output_dir prevents mutation of repository source, tests, docs, and root."""
        repo_root = Path.cwd().resolve()

        # Target pointing directly to repo root
        with pytest.raises(ValueError, match="输出目录不能直接指向项目根目录"):
            _assert_safe_output_dir(repo_root)

        # Target pointing to source code directory
        with pytest.raises(ValueError, match="Zero Workspace Mutation"):
            _assert_safe_output_dir(repo_root / "research_tool")

        # Target pointing to tests directory
        with pytest.raises(ValueError, match="Zero Workspace Mutation"):
            _assert_safe_output_dir(repo_root / "tests")

        # Target pointing to documentation directory
        with pytest.raises(ValueError, match="Zero Workspace Mutation"):
            _assert_safe_output_dir(repo_root / "docs")

        # Null byte in path
        with pytest.raises(ValueError, match="Null Byte"):
            _assert_safe_output_dir(str(repo_root / "research-output\x00payload"))

        # Allowed subdirectories must succeed
        _assert_safe_output_dir(None)
        _assert_safe_output_dir(repo_root / "research-output")
        _assert_safe_output_dir(repo_root / "work")
        _assert_safe_output_dir(repo_root / "dist")

    def test_run_web_input_validation_and_guards(self, monkeypatch: pytest.MonkeyPatch):
        """Stress test run_web Gradio generator with adversarial and malformed inputs."""
        base_args: dict[str, Any] = {
            "mode": "完整九段",
            "topic": "合法主题",
            "engines": ["web"],
            "rounds": 1,
            "style": "report",
            "max_nodes": 5,
            "min_nodes": 3,
            "do_extract": True,
            "pdf_path": "",
            "mineru_cmd": "mineru",
            "translate": False,
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "",
            "api_key": "",
            "work_dir": "./research-output",
            "qgate_max_high": 0,
            "qgate_max_total": 10,
            "max_targeted_rounds": 3,
            "max_targeted_queries": 10,
        }

        def call_web(**kw) -> str:
            args = dict(base_args)
            args.update(kw)
            gen = run_web(**args)
            return next(gen)[0]

        # 1. Empty topic
        assert "请先填写调研主题" in call_web(topic="")
        assert "请先填写调研主题" in call_web(topic="   ")

        # 2. Null byte in topic
        assert "空字节" in call_web(topic="恶意主题\x00注入")

        # 3. Null byte in work_dir
        assert "Null Byte" in call_web(work_dir="./research-output\x00escape")

        # 4. Workspace mutation attempts
        assert "Zero Workspace Mutation" in call_web(work_dir="research_tool")
        assert "Zero Workspace Mutation" in call_web(work_dir="tests")
        assert "Zero Workspace Mutation" in call_web(work_dir="docs")
        assert "输出目录不能直接指向项目根目录" in call_web(work_dir=".")

        # 5. PDF mode validations
        assert "需填写 PDF 文件" in call_web(mode="PDF 调研", pdf_path="")
        assert "空字节" in call_web(mode="PDF 调研", pdf_path="test\x00.pdf")


# ============================================================================
# Section 2: Strict Mode Enforcement (Features F10 & F09)
# ============================================================================


class TestStrictModeEnforcementAdversarial:
    """Stress testing RESEARCH_AGENT_STRICT enforcement across CLI and WebUI."""

    def test_cli_strict_mode_blocks_brief_mode(self, monkeypatch: pytest.MonkeyPatch):
        """Verify CLI strictly rejects --mode brief when RESEARCH_AGENT_STRICT=1."""
        runner = CliRunner()
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")

        res = runner.invoke(app, ["run", "测试主题", "--dry-run", "--mode", "brief"])
        assert res.exit_code != 0
        assert "安全拦截（RESEARCH_AGENT_STRICT）" in res.output
        assert "--mode brief" in res.output

    def test_cli_strict_mode_blocks_skipping_stages(self, monkeypatch: pytest.MonkeyPatch):
        """Verify CLI strictly blocks skipping any of the 9 canonical stages under strict mode."""
        runner = CliRunner()
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")

        # In fast mode, attempting to skip extract
        res = runner.invoke(
            app, ["run", "测试主题", "--dry-run", "--mode", "fast", "--skip", "extract"]
        )
        assert res.exit_code != 0
        assert "安全拦截（RESEARCH_AGENT_STRICT）" in res.output
        assert "缺失核心阶段" in res.output
        assert "'extract'" in res.output

        # In fast mode, attempting to skip inspect and targeted
        res_multi = runner.invoke(
            app,
            [
                "run",
                "测试主题",
                "--dry-run",
                "--mode",
                "fast",
                "--skip",
                "inspect",
                "--skip",
                "targeted",
            ],
        )
        assert res_multi.exit_code != 0
        assert "缺失核心阶段" in res_multi.output
        assert "'inspect'" in res_multi.output
        assert "'targeted'" in res_multi.output

    def test_cli_strict_mode_accepts_compliant_and_aliases(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Verify CLI accepts full 9-stage runs and canonical aliases under strict mode."""
        runner = CliRunner()
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")

        # Default run (native 9 stages)
        res_default = runner.invoke(app, ["run", "测试主题", "--dry-run"])
        assert res_default.exit_code == 0
        assert "将执行的阶段" in res_default.output

        # Config specifying stages with aliases ('network' and 'gate')
        cfg_file = tmp_path / "alias_config.yaml"
        cfg_file.write_text(
            """
pipeline:
  stages:
    - collect
    - clean
    - extract
    - network
    - inspect
    - targeted
    - merge
    - gate
    - report
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("RESEARCH_CONFIG", str(cfg_file))
        res_alias = runner.invoke(app, ["run", "测试主题", "--dry-run"])
        assert res_alias.exit_code == 0
        assert "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report" in res_alias.output

    def test_webui_strict_mode_enforcement(self, monkeypatch: pytest.MonkeyPatch):
        """Verify WebUI run_web strictly blocks non-compliant options when strict mode is active."""
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")

        base_args: dict[str, Any] = {
            "mode": "完整九段",
            "topic": "合规主题",
            "engines": ["web"],
            "rounds": 1,
            "style": "report",
            "max_nodes": 5,
            "min_nodes": 3,
            "do_extract": True,
            "pdf_path": "",
            "mineru_cmd": "mineru",
            "translate": False,
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "",
            "api_key": "",
            "work_dir": "./research-output",
            "qgate_max_high": 0,
            "qgate_max_total": 10,
            "max_targeted_rounds": 3,
            "max_targeted_queries": 10,
        }

        # 1. Block brief mode
        args_brief = dict(base_args, mode="brief")
        out_brief = next(run_web(**args_brief))[0]
        assert "安全拦截：当前环境开启了 RESEARCH_AGENT_STRICT" in out_brief

        # 2. Block skipping extract
        args_skip_extract = dict(base_args, do_extract=False)
        out_skip_extract = next(run_web(**args_skip_extract))[0]
        assert "安全拦截（RESEARCH_AGENT_STRICT）：严禁缩水或跳过核心阶段" in out_skip_extract

        # 3. Compliant run proceeds past security guard
        args_compliant = dict(base_args)
        out_compliant = next(run_web(**args_compliant))[0]
        assert "安全拦截" not in out_compliant
        assert "主题：合规主题" in out_compliant

    def test_strict_mode_truthiness_discrepancy(self, monkeypatch: pytest.MonkeyPatch):
        """Document empirical finding: CLI parses 'true'/'yes', whereas WebUI requires exactly '1'."""
        # CLI accepts 'true' and 'yes'
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "true")
        assert _is_strict_agent() is True

        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "yes")
        assert _is_strict_agent() is True

        # In WebUI line 403: os.environ.get("RESEARCH_AGENT_STRICT", "0") == "1"
        # Setting 'true' bypasses WebUI strict mode check
        monkeypatch.setenv("RESEARCH_AGENT_STRICT", "true")
        is_strict_webui = os.environ.get("RESEARCH_AGENT_STRICT", "0") == "1"
        assert is_strict_webui is False  # Empirical discrepancy documented


# ============================================================================
# Section 3: Config Example Template Compatibility & Boundary Stress (Feature F11)
# ============================================================================


class TestConfigExampleTemplateAdversarial:
    """Adversarial stress-testing of docs/config.example.yaml and domain models."""

    @pytest.fixture
    def example_yaml_path(self) -> Path:
        p = Path("docs/config.example.yaml")
        assert p.exists(), "docs/config.example.yaml must exist"
        return p

    def test_config_example_cleanliness_and_purged_flags(self, example_yaml_path: Path):
        """Verify docs/config.example.yaml has no corrupt bytes and no obsolete legacy flags."""
        content = example_yaml_path.read_text(encoding="utf-8")
        assert "\x00" not in content

        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

        # Ensure no active legacy blocks
        assert "deepen" not in parsed
        assert "nine_loop" not in parsed
        assert "nine_loop" not in (parsed.get("pipeline") or {})

        # Ensure no commented legacy flags
        assert "deepen_as_strategy" not in content
        assert "nine_loop.enabled" not in content
        assert "profile_iterations" not in content
        assert "max_backward_rounds" not in content

    def test_config_example_direct_load_and_models(self, example_yaml_path: Path, tmp_path: Path):
        """Verify load_config cleanly deserializes docs/config.example.yaml into 9-stage domain models."""
        cfg = load_config(example_yaml_path, overrides={"topic": "Template Test", "work_dir": str(tmp_path)})
        assert isinstance(cfg, PipelineConfig)

        # 9 stages
        expected_stages = [
            "collect",
            "clean",
            "extract",
            "knowledge",
            "inspect",
            "targeted",
            "merge",
            "qgate",
            "report",
        ]
        assert cfg.stages == expected_stages

        # Closed-loop configuration blocks
        assert isinstance(cfg.inspect, InspectConfig)
        assert cfg.inspect.enabled is True
        assert cfg.inspect.rules == ["contradiction", "orphan_node", "span_incomplete"]

        assert isinstance(cfg.targeted, TargetedConfig)
        assert cfg.targeted.max_rounds == 3
        assert cfg.targeted.max_queries == 10
        assert cfg.targeted.max_queries_per_round == 10
        assert cfg.targeted.max_results_per_query == 5

        assert isinstance(cfg.qgate, QGateConfig)
        assert cfg.qgate.max_high_findings == 0
        assert cfg.qgate.max_total_findings == 10

        assert isinstance(cfg.qgate.budget_lease, BudgetLeaseConfig)
        assert cfg.qgate.budget_lease.tokens_max == 1_000_000
        assert cfg.qgate.budget_lease.cost_max == 10.0
        assert cfg.qgate.budget_lease.wall_s_max == 600.0
        assert cfg.qgate.budget_lease.search_calls_max == 50

    def test_config_example_round_trip_modification(self, example_yaml_path: Path, tmp_path: Path):
        """Stress-test round-trip modification of all closed-loop sections in example YAML."""
        data = yaml.safe_load(example_yaml_path.read_text(encoding="utf-8"))

        # Mutate closed-loop fields
        data["inspect"]["rules"] = ["contradiction"]
        data["targeted"]["max_rounds"] = 5
        data["targeted"]["max_queries_per_round"] = 20
        data["targeted"]["max_results_per_query"] = 8
        data["qgate"]["max_high_findings"] = 1
        data["qgate"]["max_total_findings"] = 15
        data["qgate"]["budget_lease"]["tokens_max"] = 2_000_000
        data["qgate"]["budget_lease"]["cost_max"] = 20.0
        data["qgate"]["budget_lease"]["wall_s_max"] = 1200.0
        data["qgate"]["budget_lease"]["search_calls_max"] = 80

        modified_file = tmp_path / "modified_config.yaml"
        modified_file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        loaded = load_config(modified_file, overrides={"topic": "RoundTrip Test"})
        assert loaded.inspect.rules == ["contradiction"]
        assert loaded.targeted.max_rounds == 5
        assert loaded.targeted.max_queries == 20
        assert loaded.targeted.max_queries_per_round == 20
        assert loaded.targeted.max_results_per_query == 8
        assert loaded.qgate.max_high_findings == 1
        assert loaded.qgate.max_total_findings == 15
        assert loaded.qgate.budget_lease.tokens_max == 2_000_000
        assert loaded.qgate.budget_lease.cost_max == 20.0
        assert loaded.qgate.budget_lease.wall_s_max == 1200.0
        assert loaded.qgate.budget_lease.search_calls_max == 80

    def test_config_example_boundary_rejections(self, example_yaml_path: Path, tmp_path: Path):
        """Verify that invalid boundary inputs in config YAML raise ConfigValidationError."""
        raw_base = yaml.safe_load(example_yaml_path.read_text(encoding="utf-8"))

        invalid_scenarios = [
            ("negative tokens_max", {"qgate": {"budget_lease": {"tokens_max": -100}}}),
            ("negative cost_max", {"qgate": {"budget_lease": {"cost_max": -5.0}}}),
            ("zero max_rounds", {"targeted": {"max_rounds": 0}}),
            ("excessive max_rounds", {"targeted": {"max_rounds": 999}}),
            ("negative max_high_findings", {"qgate": {"max_high_findings": -1}}),
            ("invalid inspect section type", {"inspect": "invalid_string_not_dict"}),
            ("invalid qgate section type", {"qgate": ["list", "not", "dict"]}),
        ]

        for desc, patch in invalid_scenarios:
            d = copy.deepcopy(raw_base)
            for k, v in patch.items():
                d[k] = v
            p = tmp_path / f"test_{desc.replace(' ', '_')}.yaml"
            p.write_text(yaml.safe_dump(d), encoding="utf-8")
            with pytest.raises(ConfigValidationError):
                load_config(p, overrides={"topic": "Boundary Fail Test"})

    def test_targeted_config_queries_per_round_validation(self):
        """Verify TargetedConfig.max_queries_per_round enforces Field(ge=1, le=30) bounds."""
        # max_queries itself properly validates bounds
        with pytest.raises(Exception):
            TargetedConfig(max_queries=0)
        with pytest.raises(Exception):
            TargetedConfig(max_queries=100)

        # max_queries_per_round properly rejects out-of-bounds values
        with pytest.raises(Exception):
            TargetedConfig(max_queries_per_round=0)
        with pytest.raises(Exception):
            TargetedConfig(max_queries_per_round=-5)
        with pytest.raises(Exception):
            TargetedConfig(max_queries_per_round=1000)

        # Valid values are accepted and synchronized
        tc_valid = TargetedConfig(max_queries_per_round=15)
        assert tc_valid.max_queries == 15
        assert tc_valid.max_queries_per_round == 15
