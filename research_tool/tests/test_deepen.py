"""Deepen 反偏差深挖测试（mock LLM + stub 搜索）。"""

import pytest

from research_tool.infrastructure.llm import MockLLMClient
from research_tool.domain.models import CollectorConfig, CollectionStrategyConfig
from research_tool.infrastructure.search.base import SearchHit, SearchResult
from research_tool.infrastructure.stages.collector import Collector
from research_tool.infrastructure.stages.research_strategies import ResearchStrategies
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher

_LONG = "实质内容。" * 60


def _structured(prompt, schema):
    """按 schema/prompt 分流：实体拆分 + 实体多视角 + 缺口 + 矛盾。"""
    if schema.__name__ == "_Entities":
        return schema(entities=["康怡琳", "中南民族大学"])
    if "已识别实体" in prompt:  # 实体多视角查询
        return schema(queries=["Yilin Kang", "康怡琳 博士"])
    if "缺失哪些维度" in prompt:  # 缺口检测
        return schema(queries=["康怡琳 获奖", "康怡琳 研究方向"])
    return schema(queries=[])  # 无矛盾


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
            hits=[SearchHit(url=f"https://r/{q}", title=q, source_engine="web") for q in queries]
        )

    monkeypatch.setattr(collector, "search_queries", fake_search_queries)
    monkeypatch.setattr(Fetcher, "fetch", lambda self, url: _ok(url))

    cfg = CollectionStrategyConfig(depth=2, breadth=4, contradiction_check=True)
    res = await ResearchStrategies(cfg, collector, llm).collect_more("康怡琳 中南民族大学", raw)

    # 实体拆分生效
    assert res.entities == ["康怡琳", "中南民族大学"]
    flat = [q for qs in captured for q in qs]
    assert "Yilin Kang" in flat  # 机制 A：独立英文名查询（消偏差核心）
    assert "康怡琳 获奖" in flat  # 机制 B：缺口轮跑了
    # Collection is not a separate resumable stage and writes no legacy marker.
    assert not (raw / ".deepen_done").exists()
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
    cfg = CollectionStrategyConfig(entity_split=False, gap_detection=False, contradiction_check=False)
    res = await ResearchStrategies(cfg, collector, llm).collect_more("X", raw)
    assert res.entities == []  # 未拆分
    assert not (raw / ".deepen_done").exists()






def test_read_digest_truncates(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "01-x-aaaaaa.md").write_text(
        "<!-- title: 长文 -->\n<!-- source: http://x -->\n\n" + "字" * 5000,
        encoding="utf-8",
    )
    llm = MockLLMClient()
    stage = ResearchStrategies(
        CollectionStrategyConfig(per_file_chars=20, max_input_chars=200),
        Collector(CollectorConfig(), llm),
        llm,
    )
    digest = stage._read_digest(raw)
    assert "长文" in digest  # 标题保留
    assert digest.count("字") <= 20  # 每文件正文截断到 per_file_chars


async def _ok(url):
    return FetchResult(url, _LONG, ok=True)
