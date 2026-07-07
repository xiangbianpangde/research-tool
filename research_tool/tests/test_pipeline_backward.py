"""反向传播（FP09 / P2-6）测试：_invalidate_after_collect + _recollect + stream 反向循环。

R7 发现反向循环零覆盖（grep `_invalidate_after_collect|max_backward_rounds|backward` = 0
匹配）；R11 补齐。无真实网络/LLM（mock collector + MockLLMClient）。

循环语义说明（影响用例 (a) 的 max_backward_rounds 取值）：
stream() 用 `range(max_backward_rounds + 1)`，每轮正向后先判 `round_n >= max_backward_rounds`
再进反向。故 max_backward_rounds=1 时第 1 轮在反向之前就 break，只有 1 次反向评估。
要用例 (a) 的「第 0 轮 queries → 第 1 轮 no-queries → 终止」两轮评估路径成立，需
max_backward_rounds=2（第 1 轮反向得以执行并返回空 queries 触发终止）。
"""

from __future__ import annotations

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import (
    FeedbackPlan,
    OrganizeResult,
    PipelineConfig,
    StageEvent,
)
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.stages import Organizer
from research_tool.infrastructure.stages.base import has_output


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _organize_result(topic_dir) -> OrganizeResult:
    """非 None 的 OrganizeResult 占位（assess_and_feedback 被 mock，字段值不关键）。"""
    return OrganizeResult(
        main_table=topic_dir / "tree" / "main.md",
        nodes=[],
        cross_refs={},
        tree_dir=topic_dir / "tree",
    )


def _set_organize(result, topic_dir) -> None:
    result.organize_result = _organize_result(topic_dir)


def _set_failed(result, topic_dir) -> None:
    result.failed_stage = "clean"


def _noop(result, topic_dir) -> None:
    """正向不设 organize_result（保持 None）。"""


def _make_forward(behaviors):
    """构造 mock _stream_forward：每轮正向按 behaviors[i] 改 result，yield 一个 completed 事件。

    behaviors: list[callable(result, topic_dir)] — 每轮正向的行为（按调用顺序消费）。
    """
    state = {"i": 0}

    async def _forward(self, topic, topic_dir, result):
        i = state["i"]
        state["i"] += 1
        if i < len(behaviors):
            behaviors[i](result, topic_dir)
        yield StageEvent(stage="forward", status="completed", progress=1.0, message=f"forward-{i}")

    return _forward


async def _consume(pipe, topic):
    """消费 pipe.stream(topic)，返回事件列表。"""
    events = []
    async for ev in pipe.stream(topic):
        events.append(ev)
    return events


def _patch_loop(
    monkeypatch,
    pipe,
    *,
    forward_behaviors,
    assess_plans,
    track_invalidate_real=False,
):
    """patch _stream_forward / assess_and_feedback / _recollect / _invalidate_after_collect / _get_llm。

    返回 (recollect_calls, invalidate_calls) 供断言。
    track_invalidate_real=True 时 invalidate 调真实静态方法（用于 .deepen_done 契约用例）。
    """
    monkeypatch.setattr(ResearchPipeline, "_stream_forward", _make_forward(forward_behaviors))
    monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient())

    plans_iter = iter(assess_plans)

    async def _fake_assess(self_org, result, llm, topic):
        return next(plans_iter)

    monkeypatch.setattr(Organizer, "assess_and_feedback", _fake_assess)

    recollect_calls: list[list[str]] = []

    async def _fake_recollect(self, topic, topic_dir, queries):
        recollect_calls.append(list(queries))

    monkeypatch.setattr(ResearchPipeline, "_recollect", _fake_recollect)

    invalidate_calls: list = []
    if track_invalidate_real:
        real_invalidate = ResearchPipeline._invalidate_after_collect

        def _tracking_invalidate(self, topic_dir):
            invalidate_calls.append(topic_dir)
            real_invalidate(topic_dir)

        monkeypatch.setattr(ResearchPipeline, "_invalidate_after_collect", _tracking_invalidate)
    else:

        def _fake_invalidate(self, topic_dir):
            invalidate_calls.append(topic_dir)

        monkeypatch.setattr(ResearchPipeline, "_invalidate_after_collect", _fake_invalidate)

    return recollect_calls, invalidate_calls


# --------------------------------------------------------------------------- #
# 2.1 _invalidate_after_collect 文件系统契约
# --------------------------------------------------------------------------- #


class TestInvalidateAfterCollect:
    """_invalidate_after_collect：raw/ 保留；clean/extracted/tree + .deepen_done + report.* 删除。"""

    @staticmethod
    def _build_topic_dir(tmp_path):
        topic_dir = tmp_path / "topic"
        raw = topic_dir / "raw"
        raw.mkdir(parents=True)
        (raw / "01-a.md").write_text("raw A", encoding="utf-8")
        (raw / "02-b.md").write_text("raw B", encoding="utf-8")
        (raw / "sources.json").write_text("[]", encoding="utf-8")
        (raw / ".deepen_done").write_text("", encoding="utf-8")
        clean = topic_dir / "clean"
        clean.mkdir()
        (clean / "01-a.md").write_text("clean A", encoding="utf-8")
        (clean / "quality.json").write_text("{}", encoding="utf-8")
        extracted = topic_dir / "extracted"
        extracted.mkdir()
        (extracted / "entities.json").write_text("[]", encoding="utf-8")
        tree = topic_dir / "tree"
        tree.mkdir()
        (tree / "N1.md").write_text("node 1", encoding="utf-8")
        (topic_dir / "report.md").write_text("# Report", encoding="utf-8")
        (topic_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        return topic_dir

    def test_preserves_raw_removes_downstream(self, tmp_path):
        topic_dir = self._build_topic_dir(tmp_path)
        ResearchPipeline._invalidate_after_collect(topic_dir)

        raw = topic_dir / "raw"
        # raw/ 保留；原始文件名 + 内容字节一致
        assert (raw / "01-a.md").read_text(encoding="utf-8") == "raw A"
        assert (raw / "02-b.md").read_text(encoding="utf-8") == "raw B"
        assert (raw / "sources.json").read_text(encoding="utf-8") == "[]"
        # .deepen_done 已删除 → deepen 将重消化新 raw/
        assert not (raw / ".deepen_done").exists()
        # 下游目录已删除
        assert not (topic_dir / "clean").exists()
        assert not (topic_dir / "extracted").exists()
        assert not (topic_dir / "tree").exists()
        # report.* 已删除
        assert not (topic_dir / "report.md").exists()
        assert not (topic_dir / "report.html").exists()

    def test_idempotent_on_missing_paths(self, tmp_path):
        """部分目录已不存在的 topic_dir 上调用不应抛错。"""
        topic_dir = tmp_path / "topic"
        raw = topic_dir / "raw"
        raw.mkdir(parents=True)
        # clean/extracted/tree/report.* + .deepen_done 全部缺失

        ResearchPipeline._invalidate_after_collect(topic_dir)  # 不抛
        ResearchPipeline._invalidate_after_collect(topic_dir)  # 幂等
        assert raw.exists()


# --------------------------------------------------------------------------- #
# 2.2 _recollect 查询追加行为
# --------------------------------------------------------------------------- #


class TestRecollect:
    """_recollect：queries → search_queries + fetch_and_store；只追加 raw/。"""

    @pytest.mark.asyncio
    async def test_recollect_appends_to_raw_only(self, tmp_path, monkeypatch):
        topic_dir = tmp_path / "topic"
        raw = topic_dir / "raw"
        raw.mkdir(parents=True)
        cfg = PipelineConfig(topic="t", work_dir=tmp_path, stages=["collect"])
        pipe = ResearchPipeline(cfg)
        monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient())

        captured = {}

        class _SR:
            hits = [{"url": "https://example.com/x", "title": "X"}]

        class _FakeCollector:
            def __init__(self, *a, **kw):
                pass

            async def search_queries(self, queries):
                captured["queries"] = list(queries)
                return _SR()

            async def fetch_and_store(self, topic, hits, out_dir):
                captured["fetch"] = {
                    "topic": topic,
                    "hits": list(hits),
                    "out_dir": out_dir,
                }

        monkeypatch.setattr("research_tool.application.pipeline.Collector", _FakeCollector)

        await pipe._recollect("t", topic_dir, ["q1", "q2"])

        # search_queries 收到精确查询列表
        assert captured["queries"] == ["q1", "q2"]
        # fetch_and_store 收到 hits + topic + raw 目录
        assert captured["fetch"]["out_dir"] == raw
        assert captured["fetch"]["hits"] == _SR.hits
        assert captured["fetch"]["topic"] == "t"
        # 只碰 raw/，不创建 clean/extracted/tree
        assert not (topic_dir / "clean").exists()
        assert not (topic_dir / "extracted").exists()
        assert not (topic_dir / "tree").exists()


# --------------------------------------------------------------------------- #
# 2.3 stream() 反向循环控制流（5 用例）+ 2.4 .deepen_done 契约
# --------------------------------------------------------------------------- #


class TestStreamBackwardLoop:
    """stream() 反向循环控制流：终止条件 + assess→recollect→invalidate→下一轮正向。"""

    @pytest.mark.asyncio
    async def test_a_queries_then_stop(self, tmp_path, monkeypatch):
        """用例 (a)：第 0 轮 queries → 重采+失效 → 第 1 轮 no-queries → 终止（无第三轮）。

        max_backward_rounds=2（见模块 docstring 说明）：第 1 轮反向得以执行并返回空 queries。
        同时钉 .deepen_done 契约（2.4）：用真实 _invalidate_after_collect，断言删除后
        has_output(raw, ['.deepen_done']) 为 False → 第二轮 deepen 不会因 resume 跳过。
        """
        cfg = PipelineConfig(
            topic="t", work_dir=tmp_path, stages=["collect"], max_backward_rounds=2
        )
        pipe = ResearchPipeline(cfg)

        # 预建真实 .deepen_done + 下游目录（让真实 invalidate 有东西可删，验证 2.4 契约）
        topic_dir = tmp_path / "t"
        raw = topic_dir / "raw"
        raw.mkdir(parents=True)
        (raw / ".deepen_done").write_text("", encoding="utf-8")
        (topic_dir / "clean").mkdir()
        (topic_dir / "extracted").mkdir()
        (topic_dir / "tree").mkdir()

        recollect_calls, invalidate_calls = _patch_loop(
            monkeypatch,
            pipe,
            forward_behaviors=[_set_organize, _set_organize],
            assess_plans=[
                FeedbackPlan(queries=["q1"], sparse_nodes=["n1"]),
                FeedbackPlan(queries=[]),
            ],
            track_invalidate_real=True,
        )
        events = await _consume(pipe, "t")

        # 两次正向（第 0、1 轮）；无第三轮
        forward_done = [e for e in events if e.stage == "forward" and e.status == "completed"]
        assert len(forward_done) == 2
        # 反向事件：第 0 轮 started/progress/completed；第 1 轮 started/completed(无 queries)
        backward = [e for e in events if e.stage == "backward"]
        assert "started" in [e.status for e in backward]
        assert "progress" in [e.status for e in backward]
        assert "completed" in [e.status for e in backward]
        # _recollect 只在第 0 轮调用，参数为 ["q1"]
        assert recollect_calls == [["q1"]]
        # _invalidate_after_collect 只在第 0 轮调用（真实删除）
        assert len(invalidate_calls) == 1
        assert not (raw / ".deepen_done").exists()
        assert not (topic_dir / "clean").exists()
        assert not (topic_dir / "extracted").exists()
        assert not (topic_dir / "tree").exists()
        # 2.4 契约：.deepen_done 已删 → deepen 不会因 resume 跳过（has_output False）
        assert not has_output(raw, [".deepen_done"])

    @pytest.mark.asyncio
    async def test_b_no_queries_terminates(self, tmp_path, monkeypatch):
        """用例 (b)：assess 第 0 轮返回空 queries → 反向 completed 终止，无 _recollect。"""
        cfg = PipelineConfig(
            topic="t", work_dir=tmp_path, stages=["collect"], max_backward_rounds=1
        )
        pipe = ResearchPipeline(cfg)
        recollect_calls, invalidate_calls = _patch_loop(
            monkeypatch,
            pipe,
            forward_behaviors=[_set_organize],
            assess_plans=[FeedbackPlan(queries=[], sparse_nodes=[])],
        )
        events = await _consume(pipe, "t")

        # 仅一次正向
        assert len([e for e in events if e.stage == "forward" and e.status == "completed"]) == 1
        # 反向 completed 且消息含「无修正查询」
        backward_done = [e for e in events if e.stage == "backward" and e.status == "completed"]
        assert backward_done
        assert any("无修正查询" in e.message for e in backward_done)
        # 无 _recollect / 无 _invalidate
        assert recollect_calls == []
        assert invalidate_calls == []

    @pytest.mark.asyncio
    async def test_c_organize_none_is_noop(self, tmp_path, monkeypatch):
        """用例 (c)：organize_result 为 None → 反向 no-op，循环停止。"""
        cfg = PipelineConfig(
            topic="t", work_dir=tmp_path, stages=["collect"], max_backward_rounds=1
        )
        pipe = ResearchPipeline(cfg)
        recollect_calls, invalidate_calls = _patch_loop(
            monkeypatch,
            pipe,
            forward_behaviors=[_noop],  # organize_result 保持 None
            assess_plans=[],  # 不应被调用
        )
        events = await _consume(pipe, "t")

        assert len([e for e in events if e.stage == "forward"]) == 1
        assert not [e for e in events if e.stage == "backward"]
        assert recollect_calls == []
        assert invalidate_calls == []

    @pytest.mark.asyncio
    async def test_d_failed_stage_short_circuits(self, tmp_path, monkeypatch):
        """用例 (d)：正向 stage 失败 → 无反向执行（失败的正向短路）。"""
        cfg = PipelineConfig(
            topic="t", work_dir=tmp_path, stages=["collect"], max_backward_rounds=1
        )
        pipe = ResearchPipeline(cfg)
        recollect_calls, invalidate_calls = _patch_loop(
            monkeypatch,
            pipe,
            forward_behaviors=[_set_failed],  # result.failed_stage = "clean"
            assess_plans=[],  # 不应被调用
        )
        events = await _consume(pipe, "t")

        assert len([e for e in events if e.stage == "forward"]) == 1
        assert not [e for e in events if e.stage == "backward"]
        assert recollect_calls == []
        assert invalidate_calls == []
        assert pipe._result.failed_stage == "clean"

    @pytest.mark.asyncio
    async def test_e_max_zero_no_backward(self, tmp_path, monkeypatch):
        """用例 (e)：max_backward_rounds=0 → 恰好一次正向，无任何反向事件（反向 opt-in）。"""
        cfg = PipelineConfig(
            topic="t", work_dir=tmp_path, stages=["collect"], max_backward_rounds=0
        )
        pipe = ResearchPipeline(cfg)
        recollect_calls, invalidate_calls = _patch_loop(
            monkeypatch,
            pipe,
            forward_behaviors=[_set_organize],
            assess_plans=[],  # 不应被调用
        )
        events = await _consume(pipe, "t")

        assert len([e for e in events if e.stage == "forward"]) == 1
        assert not [e for e in events if e.stage == "backward"]
        assert recollect_calls == []
        assert invalidate_calls == []
