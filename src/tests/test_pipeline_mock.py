"""端到端（mock LLM，无网络）：clean → extract → organize → report。

跳过 collect（需联网），手工铺设 raw/clean，验证后续 4 个 Stage 与 Pipeline 编排。
"""

import pytest

from src.infrastructure.llm import MockLLMClient
from src.domain.models import (
    CleanerConfig,
    ExtractorConfig,
    OrganizerConfig,
    PipelineConfig,
    ReporterConfig,
)
from src.application.pipeline import ResearchPipeline
from src.infrastructure.stages import Cleaner, Extractor, Organizer, Reporter

RAW = """<!-- source: https://example.com/a -->
<!-- title: Transformer -->

The Transformer is a neural network architecture introduced in 2017 that relies entirely on self-attention mechanisms to draw global dependencies between input and output.
It replaced recurrent layers with multi-head attention and achieved state of the art on translation tasks.
"""


def _structured(prompt, schema):
    fields = set(schema.model_fields)
    if {"nodes", "main_thread"} <= fields:  # _NodePlan
        return schema(
            nodes=[
                {"title": "什么是Transformer", "core_question": "定义是什么？"},
                {"title": "核心机制", "core_question": "自注意力如何工作？"},
            ],
            main_thread="定义 → 机制",
        )
    if {"entities", "triples"} <= fields:  # _ChunkResult
        return schema(
            entities=[{"name": "Transformer", "type": "Model", "confidence": 0.9}],
            triples=[{"head": "Transformer", "relation": "uses", "tail": "self-attention"}],
        )
    return schema()


@pytest.fixture
def llm():
    return MockLLMClient(chat_response="（mock 生成的正文）", structured_response=_structured)


def test_stages_individually(tmp_path, llm):
    raw = tmp_path / "topic" / "raw"
    raw.mkdir(parents=True)
    (raw / "01-example.com.md").write_text(RAW, encoding="utf-8")

    clean_res = Cleaner(CleanerConfig(min_content_length=50)).process(raw)
    assert clean_res.files

    import asyncio

    ext = asyncio.run(Extractor(ExtractorConfig()).run(clean_res.clean_dir, llm))
    assert any(e.name == "Transformer" for e in ext.entities)
    assert ext.triples

    org = asyncio.run(Organizer(OrganizerConfig(min_nodes=2)).run(ext.output_dir, llm))
    assert len(org.nodes) == 2
    assert org.main_table.exists()

    rep = asyncio.run(Reporter(ReporterConfig()).run(org.tree_dir, llm, topic="Transformer"))
    assert rep.report_path.exists()
    assert rep.word_count > 0


@pytest.mark.asyncio
async def test_pipeline_skip_collect(tmp_path, llm, monkeypatch):
    topic_dir = tmp_path / "out" / "transformer"
    raw = topic_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "01-example.com.md").write_text(RAW, encoding="utf-8")

    cfg = PipelineConfig(
        topic="transformer",
        work_dir=tmp_path / "out",
        stages=["clean", "extract", "organize", "report"],
        cleaner=CleanerConfig(min_content_length=50),
        organizer=OrganizerConfig(min_nodes=2),
    )
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)

    result = await pipe.run("transformer")
    assert result.failed_stage is None
    assert "report" in result.stages_completed
    assert result.report_result.report_path.exists()


@pytest.mark.asyncio
async def test_pipeline_resume_skips(tmp_path, llm, monkeypatch):
    topic_dir = tmp_path / "out" / "transformer"
    raw = topic_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "01-example.com.md").write_text(RAW, encoding="utf-8")
    # 预置 clean 输出 → clean 应被跳过
    clean = topic_dir / "clean"
    clean.mkdir()
    (clean / "01-example.com.md").write_text("done", encoding="utf-8")

    cfg = PipelineConfig(
        topic="transformer",
        work_dir=tmp_path / "out",
        stages=["clean"],
    )
    pipe = ResearchPipeline(cfg)
    monkeypatch.setattr(pipe, "_get_llm", lambda: llm)
    result = await pipe.run("transformer")
    assert "clean" in result.stages_skipped
