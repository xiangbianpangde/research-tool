"""Deepen 反偏差深挖测试（mock LLM + stub 搜索）。"""

import pytest

from research_tool.llm import MockLLMClient
from research_tool.models import CollectorConfig, DeepenConfig
from research_tool.search.base import SearchHit, SearchResult
from research_tool.stages.collector import Collector
from research_tool.stages.deepen import DeepenStage
from research_tool.stages.fetcher import FetchResult, Fetcher

_LONG = "实质内容。" * 60


def _structured(prompt, schema):
    """按 schema/prompt 分流：实体拆分 + 实体多视角 + 缺口 + 矛盾。"""
    if schema.__name__ == "_Entities":
        return schema(entities=["康怡琳", "中南民族大学"])
    if "已识别实体" in prompt:                 # 实体多视角查询
        return schema(queries=["Yilin Kang", "康怡琳 博士"])
    if "缺失哪些维度" in prompt:               # 缺口检测
        return schema(queries=["康怡琳 获奖", "康怡琳 研究方向"])
    return schema(queries=[])                  # 无矛盾


@pytest.mark.asyncio
async def test_deepen_splits_entities_and_deepens(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    # 预置一篇已采资料，供缺口/矛盾检测读取
    (raw / "01-seed-aaaaaa.md").write_text(
        "<!-- title: 康怡琳-中南民族大学 -->\n\n康怡琳是中南民族大学的教师。",
        encoding="utf-8",
    )

    llm = MockLLMClient(structured_response=_structured)
    collector = Collector(CollectorConfig(depth=2), llm)

    captured: list[list[str]] = []

    async def fake_search_queries(queries):
        captured.append(list(queries))
        # 每个查询给一个独立 URL，让 fetch_and_store 真正落盘
        return SearchResult(
            hits=[
                SearchHit(url=f"https://r/{q}", title=q, source_engine="web")
                for q in queries
            ]
        )

    monkeypatch.setattr(collector, "search_queries", fake_search_queries)
    monkeypatch.setattr(Fetcher, "fetch", lambda self, url: _ok(url))

    cfg = DeepenConfig(depth=2, breadth=4, contradiction_check=True)
    res = await DeepenStage(cfg, collector, llm).run("康怡琳 中南民族大学", raw)

    # 实体拆分生效
    assert res.entities == ["康怡琳", "中南民族大学"]
    flat = [q for qs in captured for q in qs]
    assert "Yilin Kang" in flat            # 机制 A：独立英文名查询（消偏差核心）
    assert "康怡琳 获奖" in flat            # 机制 B：缺口轮跑了
    # 完成标记写入（风险 5：resume 不会误跳）
    assert (raw / ".deepen_done").exists()
    # 新文件被追加（idx 续编，不覆盖 seed）
    assert res.new_files
    assert (raw / "01-seed-aaaaaa.md").exists()


@pytest.mark.asyncio
async def test_deepen_disabled_entity_split_single_entity(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    llm = MockLLMClient(structured_response=lambda p, s: s(queries=["X 论文"]))
    collector = Collector(CollectorConfig(), llm)

    async def fake_sq(queries):
        return SearchResult(hits=[])

    monkeypatch.setattr(collector, "search_queries", fake_sq)
    cfg = DeepenConfig(entity_split=False, gap_detection=False, contradiction_check=False)
    res = await DeepenStage(cfg, collector, llm).run("X", raw)
    assert res.entities == []               # 未拆分
    assert (raw / ".deepen_done").exists()


@pytest.mark.asyncio
async def test_pipeline_deepen_stage_and_resume(monkeypatch, tmp_path):
    """deepen 在管道里跑通，且 .deepen_done 让 resume 正确跳过（风险 5）。"""
    from research_tool.models import PipelineConfig
    from research_tool.pipeline import ResearchPipeline

    topic_dir = tmp_path / "out" / "x"
    raw = topic_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "01-seed-aaaaaa.md").write_text(
        "<!-- title: seed -->\n\n" + _LONG, encoding="utf-8"
    )

    async def fake_sq(self, queries):
        return SearchResult(
            hits=[SearchHit(url=f"https://r/{queries[0]}", source_engine="web")]
        )

    monkeypatch.setattr(Collector, "search_queries", fake_sq)
    monkeypatch.setattr(Fetcher, "fetch", lambda self, url: _ok(url))
    llm = MockLLMClient(structured_response=_structured)

    cfg = PipelineConfig(topic="x", work_dir=tmp_path / "out", stages=["deepen"])
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)

    r1 = await pipe.run("x")
    assert r1.failed_stage is None
    assert "deepen" in r1.stages_completed
    assert (raw / ".deepen_done").exists()

    # 再跑一次：resume 应跳过 deepen（不再误判为未完成）
    r2 = await pipe.run("x")
    assert "deepen" in r2.stages_skipped


@pytest.mark.asyncio
async def test_pipeline_deepen_disabled_skips(monkeypatch, tmp_path):
    from research_tool.models import DeepenConfig as _DC
    from research_tool.models import PipelineConfig
    from research_tool.pipeline import ResearchPipeline

    topic_dir = tmp_path / "out" / "x"
    (topic_dir / "raw").mkdir(parents=True)
    cfg = PipelineConfig(
        topic="x", work_dir=tmp_path / "out", stages=["deepen"],
        deepen=_DC(enabled=False),
    )
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: MockLLMClient())
    r = await pipe.run("x")
    assert "deepen" in r.stages_skipped


def test_read_digest_truncates(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "01-x-aaaaaa.md").write_text(
        "<!-- title: 长文 -->\n<!-- source: http://x -->\n\n" + "字" * 5000,
        encoding="utf-8",
    )
    llm = MockLLMClient()
    stage = DeepenStage(
        DeepenConfig(per_file_chars=20, max_input_chars=200),
        Collector(CollectorConfig(), llm),
        llm,
    )
    digest = stage._read_digest(raw)
    assert "长文" in digest                  # 标题保留
    assert digest.count("字") <= 20          # 每文件正文截断到 per_file_chars


async def _ok(url):
    return FetchResult(url, _LONG, ok=True)
