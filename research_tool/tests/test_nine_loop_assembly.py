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


# --------------------------------------------------------------------------- #
# 4) deepen_as_strategy 降级策略测试：剥离独立阶段、跳过执行
# --------------------------------------------------------------------------- #

def test_deepen_as_strategy_strips_deepen_from_full_mode(tmp_path: Path) -> None:
    """deepen_as_strategy: true 时，mode: full 自动排除独立 deepen 阶段。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\n"
        "pipeline:\n  mode: full\n  work_dir: ./out\n"
        "nine_loop:\n  enabled: true\n  deepen_as_strategy: true\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert "deepen" not in cfg.stages
    assert cfg.stages == ["collect", "clean", "extract", "organize", "report"]


def test_deepen_as_strategy_strips_deepen_from_explicit_stages(tmp_path: Path) -> None:
    """deepen_as_strategy: true 时，显式配置残留的 deepen 阶段会被自动净化。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\n"
        "pipeline:\n"
        "  work_dir: ./out\n"
        "  stages:\n"
        "    - collect\n"
        "    - deepen\n"
        "    - clean\n"
        "    - extract\n"
        "    - organize\n"
        "    - report\n"
        "nine_loop:\n  enabled: true\n  deepen_as_strategy: true\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert "deepen" not in cfg.stages
    assert cfg.stages == ["collect", "clean", "extract", "organize", "report"]


@pytest.mark.asyncio
async def test_pipeline_skips_deepen_when_strategy_active(tmp_path: Path, monkeypatch) -> None:
    """即使 pipeline.stages 残留 deepen，执行引擎也会跳过并记录 skipped，绝不执行 Crawl4AI。"""
    cfg = PipelineConfig(
        topic="t",
        work_dir=tmp_path / "out",
        stages=["collect", "deepen", "clean"],
        nine_loop=NineLoopConfig(enabled=True, deepen_as_strategy=True),
    )
    pipe = ResearchPipeline(cfg)
    executed: list[str] = []

    async def fake_exec(stage, topic, topic_dir, result):
        executed.append(stage)

    monkeypatch.setattr(pipe, "_exec", fake_exec)

    events = []
    async for ev in pipe.stream("t"):
        events.append(ev)

    assert "deepen" not in executed
    assert "deepen" in pipe._result.stages_skipped
    skip_ev = [ev for ev in events if ev.stage == "deepen" and ev.status == "skipped"]
    assert len(skip_ev) == 1
    assert "deepen_as_strategy" in skip_ev[0].message


def test_cli_dry_run_with_deepen_as_strategy(tmp_path: Path) -> None:
    """CLI dry-run 在 deepen_as_strategy: true 下展示 5 阶段拓扑（无 deepen）。"""
    from typer.testing import CliRunner
    from research_tool.presentation.cli import app

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\n"
        "pipeline:\n  mode: full\n  work_dir: ./out\n"
        "nine_loop:\n  enabled: true\n  deepen_as_strategy: true\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["--config", str(cfg_path), "run", "t", "--dry-run"])
    assert result.exit_code == 0
    assert "collect → clean → extract → organize → report" in result.output
    assert "deepen" not in result.output


# --------------------------------------------------------------------------- #
# 5) Sol 终审闭环测试：Clean 状态机 Marker、DAG 依赖、Protected Output Path
# --------------------------------------------------------------------------- #

def test_clean_requires_completion_marker_when_relevance_filter_active(tmp_path: Path) -> None:
    """Sol 终审 P0 闭环：当 clean.relevance_filter=True 时，即使已有 clean/*.md 文件，
    若无 .stage-complete/clean.json marker，禁止误判为已完成。"""
    from research_tool.domain.models import CleanerConfig
    topic_dir = tmp_path / "paper"
    clean_dir = topic_dir / "clean"
    clean_dir.mkdir(parents=True)
    (clean_dir / "01.md").write_text("clean text", encoding="utf-8")

    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(relevance_filter=True),
    )
    pipe = ResearchPipeline(cfg)
    # 无 marker 时必须判定未完成，重新执行以确保语义打分闭环
    assert pipe._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is False

    # 写入成功 marker 后，resume 正确判定为已完成
    pipe._mark_stage_complete(topic_dir, "clean")
    assert pipe._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is True


def test_pipeline_stage_dag_invariants_reject_invalid_orders() -> None:
    """Sol 终审 P2 闭环：Pipeline 构造时严格校验 DAG 拓扑顺序，禁止倒置依赖。"""
    from research_tool.domain.errors import StageError

    # 倒置：extract 早于 clean
    with pytest.raises(StageError, match="clean 必须在 extract 之前执行"):
        ResearchPipeline(PipelineConfig(stages=["extract", "clean"]))

    # 倒置：organize 早于 clean
    with pytest.raises(StageError, match="clean 必须在 organize 之前执行"):
        ResearchPipeline(PipelineConfig(stages=["organize", "clean"]))

    # 倒置：organize 早于 extract
    with pytest.raises(StageError, match="extract 必须在 organize 之前执行"):
        ResearchPipeline(PipelineConfig(stages=["organize", "extract"]))

    # 倒置：report 早于 organize
    with pytest.raises(StageError, match="organize 必须在 report 之前执行"):
        ResearchPipeline(PipelineConfig(stages=["report", "organize"]))


def test_cli_output_path_protection(tmp_path: Path) -> None:
    """Sol 终审 P1 闭环：Zero Workspace Mutation 工程防线，拦截指向项目根或源码目录的 --output。"""
    from typer.testing import CliRunner
    from research_tool.presentation.cli import app

    # 指向根目录拦截
    res_root = CliRunner().invoke(app, ["run", "topic", "-o", ".", "--dry-run"])
    assert res_root.exit_code != 0
    assert "安全拦截" in res_root.output

    # 指向源码目录拦截
    res_src = CliRunner().invoke(app, ["run", "topic", "-o", "./research_tool", "--dry-run"])
    assert res_src.exit_code != 0
    assert "安全拦截" in res_src.output
