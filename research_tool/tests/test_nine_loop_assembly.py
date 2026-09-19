"""九段闭环架构原生装配与拓扑校验测试。

验证原生九段闭环阶段配置模型 (InspectConfig, TargetedConfig, QGateConfig)
与原生 9 阶段拓扑 DAG 依赖不变量。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.domain.config import load_config
from research_tool.domain.models import (
    InspectConfig,
    PipelineConfig,
    QGateConfig,
    TargetedConfig,
)
from research_tool.application.pipeline import ResearchPipeline, create_pipeline


# --------------------------------------------------------------------------- #
# 1) config 解析：原生九段配置与配置模型
# --------------------------------------------------------------------------- #

def test_config_defaults_to_native_nine_stages(tmp_path: Path) -> None:
    """未声明 stages 或 flags 时，默认加载原生 9 阶段闭环拓扑。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\npipeline:\n  work_dir: ./out\n", encoding="utf-8"
    )
    cfg = load_config(cfg_path)
    assert cfg.stages == [
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
    assert isinstance(cfg.inspect, InspectConfig)
    assert isinstance(cfg.targeted, TargetedConfig)
    assert isinstance(cfg.qgate, QGateConfig)


def test_config_schema_parses_nine_stage_blocks(tmp_path: Path) -> None:
    """显式 inspect / targeted / qgate 配置段正确解析进对应配置模型。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\n"
        "pipeline:\n"
        "  work_dir: ./out\n"
        "inspect:\n"
        "  enabled: true\n"
        "  rules: [contradiction, orphan_node]\n"
        "  min_gap_severity: high\n"
        "targeted:\n"
        "  max_rounds: 2\n"
        "  max_queries: 12\n"
        "qgate:\n"
        "  max_high_findings: 1\n"
        "  max_total_findings: 5\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.inspect.enabled is True
    assert cfg.inspect.rules == ["contradiction", "orphan_node"]
    assert cfg.inspect.min_gap_severity == "high"
    assert cfg.targeted.max_rounds == 2
    assert cfg.targeted.max_queries == 12
    assert cfg.qgate.max_high_findings == 1
    assert cfg.qgate.max_total_findings == 5


def test_cli_dry_run_native_nine_stages(tmp_path: Path) -> None:
    """CLI dry-run 展示原生 9 阶段拓扑。"""
    from typer.testing import CliRunner
    from research_tool.presentation.cli import app

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "topic: t\n"
        "pipeline:\n  mode: full\n  work_dir: ./out\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["--config", str(cfg_path), "run", "t", "--dry-run"])
    assert result.exit_code == 0
    assert "collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report" in result.output


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

    # Sol 第四轮反例：clean 不传 -o，但 input_dir 在 docs/raw，推导出的 docs/clean 必须被拦截
    docs_raw = Path.cwd() / "docs" / "raw"
    res_clean_none = CliRunner().invoke(app, ["clean", str(docs_raw)])
    assert res_clean_none.exit_code != 0
    assert "安全拦截" in res_clean_none.output

    # Sol 第四轮反例：ingest-pdf 指向 docs/ 必须被拦截
    res_ingest = CliRunner().invoke(app, ["ingest-pdf", "dummy.pdf", "-T", "demo", "-o", "./docs"])
    assert res_ingest.exit_code != 0
    assert "安全拦截" in res_ingest.output

    # Sol 第五轮反例：-o ./work 参数看似在白名单，但 stage 对 output.parent 计算后落于 /repo/clean、/repo/extracted、/repo/tree
    res_clean_work = CliRunner().invoke(app, ["clean", str(docs_raw), "-o", "./work"])
    assert res_clean_work.exit_code != 0
    assert "安全拦截" in res_clean_work.output

    res_extract_work = CliRunner().invoke(app, ["extract", str(docs_raw), "-o", "./work"])
    assert res_extract_work.exit_code != 0
    assert "安全拦截" in res_extract_work.output

    res_org_work = CliRunner().invoke(app, ["organize", str(docs_raw), "-o", "./work"])
    assert res_org_work.exit_code != 0
    assert "安全拦截" in res_org_work.output


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


def test_clean_marker_covers_all_cleaner_config_fields(tmp_path: Path) -> None:
    """Sol 第四轮 P0-C 反例闭环：strip_nav/find_content_start/min_content_length
    变动时，必须使旧 marker 失效，绝不能漏掉 CleanerConfig 任何字段。"""
    from research_tool.domain.models import CleanerConfig
    topic_dir = tmp_path / "paper"
    raw_dir = topic_dir / "raw"
    clean_dir = topic_dir / "clean"
    raw_dir.mkdir(parents=True)
    clean_dir.mkdir(parents=True)
    (raw_dir / "01.md").write_text("raw text", encoding="utf-8")
    (clean_dir / "01.md").write_text("clean text", encoding="utf-8")

    # 1. 初始配置 A
    cfg_a = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(strip_nav=True, find_content_start=True, min_content_length=200),
    )
    pipe_a = ResearchPipeline(cfg_a)
    pipe_a._mark_stage_complete(topic_dir, "clean")
    assert pipe_a._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is True

    # 2. 修改未显式挑选的字段：strip_nav 改为 False
    cfg_b = PipelineConfig(
        topic="paper",
        work_dir=tmp_path,
        stages=["clean"],
        cleaner=CleanerConfig(strip_nav=False, find_content_start=True, min_content_length=200),
    )
    pipe_b = ResearchPipeline(cfg_b)
    # 必须识别出配置指纹变动，marker 失效
    assert pipe_b._stage_is_complete("clean", topic_dir, clean_dir, ["*.md"]) is False


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
