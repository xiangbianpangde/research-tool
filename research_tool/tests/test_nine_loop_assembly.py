"""B8 产品集成装配测试（touch 5）—— flag-on 装配 + shadow 根接线。

零模型调用：只测 config 解析 / flag 解析 / shadow 根路径逻辑。
flag off 逐字节回归由现有测试套件全量保证。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.domain.config import load_config
from research_tool.domain.models import NineLoopConfig, PipelineConfig
from research_tool.application.pipeline import ResearchPipeline, create_pipeline


# --------------------------------------------------------------------------- #
# 1) config 解析：nine_loop 段 → PipelineConfig.nine_loop（默认全关）
# --------------------------------------------------------------------------- #

def test_config_schema_defaults_on(tmp_path: Path) -> None:
    """未声明 nine_loop 段 → 默认 ON（P7-T9 默认切换：九段为默认路径）。

    子 flag（gate/shadow/stages）仍默认关；kill-switch 角色反转：
    enabled=False = 显式 legacy 回退。
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\npipeline:\n  work_dir: ./out\n", encoding="utf-8"
    )
    cfg = load_config(cfg_path)
    assert cfg.nine_loop.enabled is True
    assert cfg.nine_loop.gate_enabled is False
    assert cfg.nine_loop.shadow_enabled is False
    assert cfg.nine_loop.shadow_sample_rate == 0.0
    assert all(v is False for v in cfg.nine_loop.stages.values())


def test_config_schema_parses_nine_loop_section(tmp_path: Path) -> None:
    """显式 nine_loop 段 → 正确解析进 PipelineConfig。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\npipeline:\n  work_dir: ./out\n"
        "nine_loop:\n  enabled: true\n  shadow_sample_rate: 0.5\n"
        "  stages:\n    report: true\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.nine_loop.enabled is True
    assert cfg.nine_loop.shadow_sample_rate == 0.5
    assert cfg.nine_loop.stages["report"] is True
    assert cfg.nine_loop.stages["collect"] is False  # 其余仍默认关


# --------------------------------------------------------------------------- #
# 2) flag 解析装配点：kill-switch=false → 空快照；enabled → resolve() 快照
# --------------------------------------------------------------------------- #

def test_assembly_kill_switch_off_empty_snapshot() -> None:
    """kill-switch=false（显式）：装配点空快照 = legacy 回退（逐字节）。"""
    pipe = ResearchPipeline(PipelineConfig(
        topic="t", nine_loop=NineLoopConfig(enabled=False)))
    fl = pipe.nine_loop_flags()
    assert fl == {}
    # 空快照确保不含任何子 flag
    assert "nine_loop.stages.collect" not in fl
    assert "nine_loop.shadow.enabled" not in fl


def test_assembly_default_on_snapshot() -> None:
    """默认（未显式设置）：九段默认激活（P7-T9）。"""
    pipe = ResearchPipeline(PipelineConfig(topic="t"))
    fl = pipe.nine_loop_flags()
    assert fl.get("nine_loop_enabled") is True
    assert fl.get("legacy") is False


def test_assembly_enabled_resolves_snapshot() -> None:
    """kill-switch=true + 子 flag：resolve() 决策快照含完整键集且子 flag 生效。"""
    cfg = PipelineConfig(
        topic="t",
        nine_loop=NineLoopConfig(
            enabled=True,
            gate_enabled=True,
            shadow_enabled=True,
            shadow_sample_rate=0.25,
            stages={"collect": True, "report": False},
        ),
    )
    pipe = ResearchPipeline(cfg)
    fl = pipe.nine_loop_flags()
    assert fl["legacy"] is False
    assert fl["nine_loop_enabled"] is True
    assert fl["gate_enabled"] is True
    assert fl["shadow_enabled"] is True
    assert fl["sample_rate"] == 0.25
    assert fl["stages"]["collect"] is True
    assert fl["stages"]["report"] is False
    assert fl["stages"]["merge"] is False  # 未声明 → 默认关


# --------------------------------------------------------------------------- #
# 3) shadow 根接线：work_dir 旁 shadow/<name>/，不创建目录，永不写 legacy
# --------------------------------------------------------------------------- #

def test_shadow_root_isolated(tmp_path: Path) -> None:
    """shadow 根 = work_dir.parent/shadow/<work_dir.name>，与 legacy 输出无交集。"""
    wd = tmp_path / "data" / "topic-a"
    pipe = ResearchPipeline(PipelineConfig(topic="t", work_dir=wd))
    root = pipe.shadow_root()
    assert root == tmp_path / "data" / "shadow" / "topic-a"
    # 默认关：绝不创建目录
    assert not root.exists()
    # legacy 输出根（work_dir 本身）不在 shadow 根内
    assert wd not in root.parents
    assert root not in wd.parents or False


def test_create_pipeline_preserves_defaults() -> None:
    """create_pipeline 工厂默认 = 九段默认路径（P7-T9）。"""
    pipe = create_pipeline(PipelineConfig(topic="t"))
    fl = pipe.nine_loop_flags()
    assert fl.get("nine_loop_enabled") is True
    assert fl.get("legacy") is False
