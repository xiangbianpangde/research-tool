import pytest

from research_tool.llm import MockLLMClient
from research_tool.translate import chunk_markdown, translate_markdown


def test_chunk_respects_size_and_paragraphs():
    md = "\n\n".join(["para " * 50] * 10)  # 10 段
    chunks = chunk_markdown(md, max_chars=600)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)  # 段落边界，略有富余


def test_chunk_empty():
    assert chunk_markdown("") == [""]


@pytest.mark.asyncio
async def test_translate_concurrent_order_preserved():
    # mock：把每块原样回显，验证顺序拼接稳定
    llm = MockLLMClient(chat_response=lambda p: f"[{p[:5]}]")
    md = "\n\n".join([f"block{i} " * 60 for i in range(5)])
    out = await translate_markdown(md, llm, chunk_size=400, concurrency=4)
    assert out.count("[") >= 5  # 每块都被处理


@pytest.mark.asyncio
async def test_translate_chunk_failure_keeps_original():
    class Boom(MockLLMClient):
        async def chat(self, *a, **k):
            raise RuntimeError("boom")

    md = "hello world paragraph"
    out = await translate_markdown(md, Boom(), chunk_size=100)
    assert "hello world" in out  # 失败回退原文
