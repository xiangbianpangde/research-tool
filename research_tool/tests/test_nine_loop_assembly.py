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

    # Sol 反例：指向 docs 或 tests 拦截
    res_docs = CliRunner().invoke(app, ["run", "topic", "-o", "./docs", "--dry-run"])
    assert res_docs.exit_code != 0
    assert "安全拦截" in res_docs.output

    res_tests = CliRunner().invoke(app, ["run", "topic", "-o", "./tests", "--dry-run"])
    assert res_tests.exit_code != 0
    assert "安全拦截" in res_tests.output


def test_clean_marker_bound_to_relevance_config_rejects_stale_marker(tmp_path: Path) -> None:
    """Sol 终审 P0-C 反例闭环：先在 relevance_filter=False 下跑出 marker，
    随后切到 relevance_filter=True，旧 marker 必须判定失效并强制重新执行语义过滤。"""
    from research_tool.domain.models import CleanerConfig
    topic_dir = tmp_path / "paper"
    clean_dir = topic_dir / "clean"
    clean_dir.mkdir(parents=True)
    (clean_dir / "01.md").write_text("clean text", encoding="utf-8")

    # 1. 在 relevance_filter=False 时写入旧 marker
    cfg_false = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(relevance_filter=False),
    )
    pipe_false = ResearchPipeline(cfg_false)
    pipe_false._mark_stage_complete(topic_dir, "clean")

    # 2. 切换到 relevance_filter=True，验证旧 marker 不能绕过新打分
    cfg_true = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(relevance_filter=True),
    )
    pipe_true = ResearchPipeline(cfg_true)
    assert pipe_true._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is False


def test_clean_marker_detects_raw_content_mutation_and_invalidates(tmp_path: Path) -> None:
    """Sol 终审 P0-C 机械反例闭环：文件名相同但 raw 内容发生变化时，旧 marker 必须立即失效并强制重新清洗。"""
    from research_tool.domain.models import CleanerConfig
    topic_dir = tmp_path / "paper"
    raw_dir = topic_dir / "raw"
    clean_dir = topic_dir / "clean"
    raw_dir.mkdir(parents=True)
    clean_dir.mkdir(parents=True)

    # 1. 初始内容 A
    raw_file = raw_dir / "01.md"
    raw_file.write_text("old raw content A", encoding="utf-8")
    (clean_dir / "01.md").write_text("cleaned A", encoding="utf-8")

    cfg = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(relevance_filter=True),
    )
    pipe = ResearchPipeline(cfg)
    pipe._mark_stage_complete(topic_dir, "clean")
    assert pipe._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is True

    # 2. 发生变更：同名文件 01.md 被更新为内容 B
    raw_file.write_text("COMPLETELY DIFFERENT NEW PAYLOAD B", encoding="utf-8")

    # 3. 校验：指纹识别到真实内容哈希变化，marker 必须判定失效
    assert pipe._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is False


def test_agent_strict_blocks_stage_skipping_and_brief(tmp_path: Path, monkeypatch) -> None:
    """Sol 终审 P0-B 反例闭环：RESEARCH_AGENT_STRICT 模式下，任何通过 config brief、
    --mode fast --skip extract 等组合偷懒跳过核心阶段的行为均被硬拦截。"""
    from typer.testing import CliRunner
    from research_tool.presentation.cli import app

    monkeypatch.setenv("RESEARCH_AGENT_STRICT", "1")

    # 绕过 A：config 中声明 brief
    cfg_brief = tmp_path / "config_brief.yaml"
    cfg_brief.write_text("pipeline:\n  mode: brief\n  work_dir: ./research-output\n", encoding="utf-8")
    res_a = CliRunner().invoke(app, ["--config", str(cfg_brief), "run", "topic", "--dry-run"])
    assert res_a.exit_code != 0
    assert "安全拦截（RESEARCH_AGENT_STRICT）" in res_a.output

    # 绕过 B：--mode fast --skip extract --skip organize 试图凑出 collect-clean-report
    cfg_normal = tmp_path / "config_normal.yaml"
    cfg_normal.write_text("pipeline:\n  mode: standard\n  work_dir: ./research-output\n", encoding="utf-8")
    res_b = CliRunner().invoke(
        app,
        ["--config", str(cfg_normal), "run", "topic", "--mode", "fast", "--skip", "extract", "--skip", "organize", "--dry-run"],
    )
    assert res_b.exit_code != 0
    assert "安全拦截（RESEARCH_AGENT_STRICT）" in res_b.output
    assert "extract" in res_b.output or "organize" in res_b.output


def test_pipeline_dag_predecessor_invariants() -> None:
    """Sol 终审 P1/P2-F 闭环：多阶段管线必须满足前置阶段依赖，禁止跳层断裂。"""
    from research_tool.domain.errors import StageError

    # 倒置：clean 早于 collect
    with pytest.raises(StageError, match="collect 必须在 clean 之前执行"):
        ResearchPipeline(PipelineConfig(stages=["clean", "collect", "extract", "organize", "report"]))

    # collect 直接跳到 extract（缺失 clean 洗涤）
    with pytest.raises(StageError, match="从 collect 到 extract 必须经过 clean"):
        ResearchPipeline(PipelineConfig(stages=["collect", "extract"]))

    # clean 直接跳到 report（非 brief 模式缺失 organize 知识树构建）
    with pytest.raises(StageError, match="从 clean 到 report 必须经过 organize"):
        ResearchPipeline(PipelineConfig(stages=["clean", "report"], mode="full"))

    # extract 直接跳到 report（非 brief 模式缺失 organize 知识树构建）
    with pytest.raises(StageError, match="从 extract 到 report 必须经过 organize"):
        ResearchPipeline(PipelineConfig(stages=["extract", "report"], mode="full"))

    # collect 直接跳到 report（缺失 clean）
    with pytest.raises(StageError, match="从 collect 到 report 必须经过 clean"):
        ResearchPipeline(PipelineConfig(stages=["collect", "report"]))


def test_tavily_key_rotation_atomic_reservation(monkeypatch) -> None:
    """Sol 终审 P1-D 闭环：当 Key 遭遇配额超额时，原子加入黑名单，后续分配绝对不再尝试该 Key。"""
    from unittest.mock import MagicMock
    from tavily import UsageLimitExceededError
    from research_tool.infrastructure.search.tavily import TavilyBackend

    monkeypatch.delenv("TAVILY_KEYS", raising=False)
    backend = TavilyBackend(api_key="key1,key2")
    # 模拟 key1 429 报错，key2 成功返回
    calls = []

    def mock_search_with_key(key, query, max_results):
        calls.append(key)
        if key == "key1":
            raise UsageLimitExceededError("key1 quota exhausted")
        hit = MagicMock()
        hit.url = "http://test.com"
        return [hit]

    backend._search_with_key = mock_search_with_key

    # 第一次查询：key1 报错拉黑，自动轮换 key2 成功
    res1 = backend._search_sync("q1", 5)
    assert len(res1) == 1
    assert "key1" in backend._exhausted_keys

    # 第二次查询：由于 key1 已在黑名单，必须直接分配 key2，绝不能再次访问 key1
    calls.clear()
    res2 = backend._search_sync("q2", 5)
    assert len(res2) == 1
    assert calls == ["key2"]  # 绝对没有 key1


def test_tavily_concurrent_in_flight_reservation(monkeypatch) -> None:
    """Sol 终审 P1-D 并发闭环：多并发线程访问 Tavily 时，在途请求排他预占 key，
    杜绝 threads > keys 时多个线程同时撞向同一个 key。"""
    import threading
    import time
    from unittest.mock import MagicMock
    from research_tool.infrastructure.search.tavily import TavilyBackend

    monkeypatch.delenv("TAVILY_KEYS", raising=False)
    backend = TavilyBackend(api_key="key1,key2")
    active_in_flight = {"key1": 0, "key2": 0}
    max_simultaneous = {"key1": 0, "key2": 0}
    lock = threading.Lock()

    def mock_search_with_key(key, query, max_results):
        with lock:
            active_in_flight[key] += 1
            max_simultaneous[key] = max(max_simultaneous[key], active_in_flight[key])
        time.sleep(0.03)
        with lock:
            active_in_flight[key] -= 1
        hit = MagicMock()
        hit.url = "http://test.com"
        return [hit]

    backend._search_with_key = mock_search_with_key

    threads = [
        threading.Thread(target=backend._search_sync, args=(f"q{i}", 5))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 验证：8 线程并发访问 2 keys，每个 key 同时在途的请求数恒 <= 1（完全排他 reservation）
    assert max_simultaneous["key1"] <= 1
    assert max_simultaneous["key2"] <= 1
