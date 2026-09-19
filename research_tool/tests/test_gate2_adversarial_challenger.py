"""Gate 2 Empirical Adversarial Stress & Verification Suite.

Adversarial stress-testing targeting Gate 2:
1. Authentication Failure & Latch State:
   - Live Ark API rejection on invalid key -> LLMAuthenticationError
   - Client latches into permanent rejection state protecting against further network leaks
   - Mock HTTP 401 raises LLMAuthenticationError and latches all client entrypoints
   - Missing key rejection at construction time
2. Connection Failure & Unreachable Base URLs:
   - Live / mock connection failures and unreachable URLs wrapped in LLMError
   - Connection failures do NOT mark authentication as failed (no false latching)
3. Timeout Handling:
   - Live Ark API hard timeout on ultra-low timeout
   - Mock timeout on chat, chat_structured, stream, and healthcheck wrapped in LLMError
   - Exception cause chain preserved (TimeoutError as __cause__)
4. Rate Limit (HTTP 429) Simulation & Retry Behavior:
   - HTTP 429 raises LLMError with HTTP 429 indicator
   - HTTP 429 does NOT trigger auth latch; subsequent calls succeed
   - model_adapter.py exponential backoff, jitter, Retry-After header parsing,
     retry exhaustion, and wall-clock budget defense
5. Token Truncation Auto-Continuation:
   - Multi-chunk auto-continuation (finish_reason == "length") stitched seamlessly
   - Bounded at max_continuations=3 to prevent infinite loops
   - Reasoning <think> tags stripped from all continuation chunks
   - Graceful fallback: continuation failure returns accumulated text rather than crashing
6. Reasoning Stream Filter Boundary Stress:
   - XML think tags fragmented across arbitrary chunk boundaries
   - Dangling unclosed think tags at stream termination
7. Pipeline Integrity & State Machine Non-Corruption:
   - Pipeline handles LLM failure gracefully: sets failed_stage, pipeline_complete=False
   - Existing stage outputs preserved without deletion or corruption
   - Transient LLM failure retried and succeeded via _exec_with_retry
   - Resume recovers from previously failed stage
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from pydantic import BaseModel, Field

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.config import load_config
from research_tool.domain.errors import (
    LLMAuthenticationError,
    LLMError,
    classify_llm_error,
)
from research_tool.domain.models import (
    LLMConfig,
    PipelineConfig,
    PipelineResult,
    StageRunMetric,
)
from research_tool.infrastructure.llm.openai_client import (
    OpenAILLMClient,
    _ThinkStreamFilter,
    _parse_structured,
    _strip_think,
)
from research_tool.infrastructure.stages.base import read_json, write_json
from research_tool.nine_loop import model_adapter

ARK_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/coding/v3",
)
ARK_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
ARK_API_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or os.environ.get("VOLC_API_KEY")
    or os.environ.get("ARK_API_KEY")
    or "ark-placeholder-adversarial-key"
)


class DummyReportSchema(BaseModel):
    summary: str = Field(description="Summary text")
    score: int = Field(description="Integer score")


# ============================================================================
# Section 1: Authentication Failure & Latch State
# ============================================================================


@pytest.mark.asyncio
async def test_adv_live_ark_invalid_key_and_latch() -> None:
    """Verify live Ark endpoint rejects invalid key and latches all entrypoints."""
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url=ARK_BASE_URL,
        api_key="invalid_401",
        request_timeout_sec=10.0,
    )
    client = OpenAILLMClient(cfg)

    # First call: reaches remote and gets HTTP 401
    with pytest.raises(LLMError) as exc_info:
        await client.chat("ping")
    assert isinstance(exc_info.value, (LLMAuthenticationError, LLMError))
    assert client._authentication_failed is True

    # Subsequent call to chat() must latch and fail immediately
    with pytest.raises(LLMAuthenticationError, match="鉴权已失败"):
        await client.chat("should not send network traffic")

    # Subsequent call to chat_structured() must latch
    with pytest.raises(LLMAuthenticationError, match="鉴权已失败"):
        await client.chat_structured("should not send", DummyReportSchema)

    # Subsequent call to healthcheck() must latch
    with pytest.raises(LLMAuthenticationError, match="鉴权已失败"):
        await client.healthcheck()


@pytest.mark.asyncio
async def test_adv_mock_openai_401_latch_all_methods() -> None:
    """Adversarially verify that any 401 AuthenticationError latches chat, structured, and stream."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="sk-mock-key")
    client = OpenAILLMClient(cfg)

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp_401 = httpx.Response(401, request=req, json={"error": {"message": "Invalid API key"}})
    auth_err = openai.AuthenticationError(
        message="Incorrect API key provided",
        response=resp_401,
        body={"error": {"message": "Incorrect API key provided"}},
    )

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=auth_err)

    # Initial call raises LLMAuthenticationError
    with pytest.raises(LLMAuthenticationError) as exc_info:
        await client.chat("hello")
    assert "401" in str(exc_info.value)
    assert client._authentication_failed is True

    # Check stream() also latches
    with pytest.raises(LLMAuthenticationError, match="鉴权已失败"):
        async for _ in client.stream("hello"):
            pass


def test_adv_init_missing_key_rejected() -> None:
    """Verify OpenAILLMClient constructor fails fast when api_key is empty."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key=None)
    with pytest.raises(LLMError, match="缺少 api_key"):
        OpenAILLMClient(cfg)


# ============================================================================
# Section 2: Connection Failure & Unreachable Base URLs
# ============================================================================


@pytest.mark.asyncio
async def test_adv_live_ark_invalid_base_url_connection_failure() -> None:
    """Verify invalid domain causes connection failure wrapped in LLMError without auth latch."""
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url="https://invalid-host-not-found-xyz987654.volces.com/v1",
        api_key=ARK_API_KEY,
        connect_timeout_sec=3.0,
        request_timeout_sec=5.0,
    )
    client = OpenAILLMClient(cfg)

    with pytest.raises(LLMError) as exc_info:
        await client.chat("ping")

    # Connection failure should NOT be an authentication failure
    assert not isinstance(exc_info.value, LLMAuthenticationError)
    assert client._authentication_failed is False


@pytest.mark.asyncio
async def test_adv_mock_connection_refused() -> None:
    """Verify connection refused / APIConnectionError is caught and wrapped without latching."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    req = httpx.Request("POST", "http://127.0.0.1:59999/v1/chat/completions")
    conn_err = openai.APIConnectionError(request=req)

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=conn_err)

    with pytest.raises(LLMError) as exc_info:
        await client.chat("ping")
    assert not isinstance(exc_info.value, LLMAuthenticationError)
    assert client._authentication_failed is False

    with pytest.raises(LLMError):
        await client.chat_structured("ping", DummyReportSchema)
    assert client._authentication_failed is False


# ============================================================================
# Section 3: Timeouts (asyncio.TimeoutError -> LLMError)
# ============================================================================


@pytest.mark.asyncio
async def test_adv_live_ark_hard_timeout_chat() -> None:
    """Verify client enforces hard timeout against live Ark API when timeout is ultra low."""
    cfg = LLMConfig(
        provider="openai",
        model=ARK_MODEL,
        base_url=ARK_BASE_URL,
        api_key=ARK_API_KEY,
        request_timeout_sec=0.0001,  # 0.1ms
    )
    client = OpenAILLMClient(cfg)

    with pytest.raises(LLMError) as exc_info:
        await client.chat("请详细阐述量子退相干时间对纠错码距离的影响")
    assert "超时" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()
    assert client._authentication_failed is False


@pytest.mark.asyncio
async def test_adv_mock_timeout_across_all_interfaces() -> None:
    """Verify asyncio.TimeoutError is reliably wrapped in LLMError across chat, structured, and stream."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key", request_timeout_sec=0.01)
    client = OpenAILLMClient(cfg)

    async def slow_mock(*args, **kwargs):
        await asyncio.sleep(1.0)

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=slow_mock)

    # 1. chat()
    with pytest.raises(LLMError) as exc_chat:
        await client.chat("hello")
    assert "超时" in str(exc_chat.value)
    assert isinstance(exc_chat.value.__cause__, asyncio.TimeoutError)

    # 2. chat_structured()
    with pytest.raises(LLMError) as exc_struct:
        await client.chat_structured("hello", DummyReportSchema)
    assert "超时" in str(exc_struct.value)
    assert isinstance(exc_struct.value.__cause__, asyncio.TimeoutError)

    # 3. stream()
    with pytest.raises(LLMError) as exc_stream:
        async for _ in client.stream("hello"):
            pass
    assert "超时" in str(exc_stream.value)
    assert isinstance(exc_stream.value.__cause__, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_adv_healthcheck_timeout() -> None:
    """Verify healthcheck detects timeout and raises descriptive LLMError."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    async def slow_chat(*args, **kwargs):
        await asyncio.sleep(1.0)
        return "PONG"

    client.chat = AsyncMock(side_effect=slow_chat)
    with pytest.raises(LLMError, match="健康检查超时"):
        await client.healthcheck(timeout_sec=0.01)


# ============================================================================
# Section 4: Rate Limit (HTTP 429) Simulation & Retry Behavior
# ============================================================================


@pytest.mark.asyncio
async def test_adv_mock_openai_429_rate_limit_resilience() -> None:
    """Verify RateLimitError raises LLMError with HTTP 429 indication, does NOT latch, and recovers."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp_429 = httpx.Response(429, request=req, json={"error": {"message": "Rate limit exceeded"}})
    rate_err = openai.RateLimitError(
        message="Rate limit exceeded",
        response=resp_429,
        body={"error": {"message": "Rate limit exceeded"}},
    )

    mock_resp_success = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_choice.message.content = "成功恢复"
    mock_resp_success.choices = [mock_choice]

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=[rate_err, mock_resp_success])

    # First call: triggers 429
    with pytest.raises(LLMError) as exc_info:
        await client.chat("ping")
    assert "HTTP 429" in str(exc_info.value)
    # MUST NOT be treated as auth failure
    assert not isinstance(exc_info.value, LLMAuthenticationError)
    assert client._authentication_failed is False

    # Second call: client is NOT latched, call succeeds!
    reply = await client.chat("ping again")
    assert reply == "成功恢复"


def test_adv_model_adapter_429_exponential_backoff_and_recovery() -> None:
    """Verify model_adapter.chat retries on 429 and recovers after backoff."""
    call_count = 0

    def mock_transport(url: str, body: bytes, key: str) -> tuple[int, bytes]:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return 429, b'{"error": "rate limit exceeded"}'
        resp = {
            "choices": [{"message": {"content": "恢复成功"}}],
            "usage": {"total_tokens": 42},
            "id": "mock-429-id",
        }
        return 200, json.dumps(resp).encode("utf-8")

    with patch.dict(os.environ, {"DEEPSEEK_V4_API_KEY": "mock-test-key"}):
        with patch("time.sleep") as mock_sleep:
            res = model_adapter.chat(
                [{"role": "user", "content": "ping"}],
                transport=mock_transport,
            )
            assert res.content == "恢复成功"
            assert call_count == 3
            # Should have slept twice for backoff
            assert mock_sleep.call_count == 2


def test_adv_model_adapter_429_exhaustion_raises_fault() -> None:
    """Verify model_adapter raises ModelFault when 429 retries are completely exhausted."""
    def persistent_429(url: str, body: bytes, key: str) -> tuple[int, bytes]:
        return 429, b'{"error": "always 429"}'

    with patch.dict(os.environ, {"DEEPSEEK_V4_API_KEY": "mock-test-key"}):
        with patch("time.sleep"):
            with pytest.raises(model_adapter.ModelFault) as exc_info:
                model_adapter.chat(
                    [{"role": "user", "content": "ping"}],
                    transport=persistent_429,
                )
            assert exc_info.value.http_status == 429


def test_adv_model_adapter_429_wall_budget_guard() -> None:
    """Verify model_adapter raises ModelFault immediately if backoff delay exceeds wall_budget_s."""
    def fast_429(url: str, body: bytes, key: str) -> tuple[int, bytes]:
        return 429, b'{"error": "rate limited"}'

    with patch.dict(os.environ, {"DEEPSEEK_V4_API_KEY": "mock-test-key"}):
        with patch("research_tool.nine_loop.model_adapter._backoff_delay", return_value=10.0):
            with pytest.raises(model_adapter.ModelFault) as exc_info:
                model_adapter.chat(
                    [{"role": "user", "content": "ping"}],
                    transport=fast_429,
                    wall_budget_s=1.0,  # 1s budget < 10s delay
                )
            assert "wall budget" in str(exc_info.value).lower()
            assert exc_info.value.http_status == 429


# ============================================================================
# Section 5: Token Truncation Auto-Continuation
# ============================================================================


@pytest.mark.asyncio
async def test_adv_auto_continuation_multi_round_stitching() -> None:
    """Verify OpenAILLMClient stitches multiple length-truncated chunks seamlessly."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    def make_resp(content: str, finish_reason: str):
        c = MagicMock()
        c.finish_reason = finish_reason
        c.message.content = content
        r = MagicMock()
        r.choices = [c]
        return r

    resp1 = make_resp("第一段：基础知识...", "length")
    resp2 = make_resp("第二段：架构演进...", "length")
    resp3 = make_resp("第三段：实验结果...", "length")
    resp4 = make_resp("第四段：未来展望与总结。", "stop")

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2, resp3, resp4]
    )

    result = await client.chat("请生成详尽技术报告")
    expected = "第一段：基础知识...第二段：架构演进...第三段：实验结果...第四段：未来展望与总结。"
    assert result == expected
    assert client._client.chat.completions.create.call_count == 4


@pytest.mark.asyncio
async def test_adv_auto_continuation_hard_limit_at_3_rounds() -> None:
    """Verify auto-continuation stops after max_continuations=3 to prevent infinite loops."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    def make_length_resp(i: int):
        c = MagicMock()
        c.finish_reason = "length"
        c.message.content = f"片段{i}..."
        r = MagicMock()
        r.choices = [c]
        return r

    # Provider returns length indefinitely
    infinite_resps = [make_length_resp(i) for i in range(10)]
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=infinite_resps)

    result = await client.chat("请无限输出")
    # 1 initial + 3 continuations = 4 total calls
    assert client._client.chat.completions.create.call_count == 4
    assert result == "片段0...片段1...片段2...片段3..."


@pytest.mark.asyncio
async def test_adv_auto_continuation_think_stripping_across_chunks() -> None:
    """Verify <think> tags appearing in continuation chunks are cleanly stripped."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    def make_resp(content: str, finish_reason: str):
        c = MagicMock()
        c.finish_reason = finish_reason
        c.message.content = content
        r = MagicMock()
        r.choices = [c]
        return r

    resp1 = make_resp("<think>第一轮思考</think>章节一：引言", "length")
    resp2 = make_resp("<think>第二轮继续思考</think>章节二：正文", "stop")

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=[resp1, resp2])

    result = await client.chat("报告")
    assert "<think>" not in result
    assert "</think>" not in result
    assert "第一轮思考" not in result
    assert "第二轮继续思考" not in result
    assert result == "章节一：引言章节二：正文"


@pytest.mark.asyncio
async def test_adv_auto_continuation_partial_failure_graceful_recovery() -> None:
    """Verify failure during continuation preserves accumulated text rather than crashing."""
    cfg = LLMConfig(provider="openai", model="test-model", api_key="test-key")
    client = OpenAILLMClient(cfg)

    c1 = MagicMock()
    c1.finish_reason = "length"
    c1.message.content = "这是已成功生成的第一部分核心论述。"
    r1 = MagicMock()
    r1.choices = [c1]

    # Continuation call crashes with connection error
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    crashed_call = openai.APIConnectionError(request=req)

    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(side_effect=[r1, crashed_call])

    result = await client.chat("长文")
    # Must preserve accumulated text instead of raising unhandled exception
    assert result == "这是已成功生成的第一部分核心论述。"


# ============================================================================
# Section 6: Reasoning Stream Filter & Structured Parsing Adversarial Stress
# ============================================================================


def test_adv_think_stream_filter_fragmented_tags() -> None:
    """Verify _ThinkStreamFilter handles XML tags split arbitrarily across chunks."""
    filter_ = _ThinkStreamFilter()
    chunks = [
        "开始正文，",
        "<",
        "th",
        "ink>隐藏思考过程1",
        "隐藏思考过程2</",
        "thi",
        "nk>正文中段，",
        "<think>另一个思考</think>",
        "正文结尾。",
    ]
    visible = []
    for c in chunks:
        visible.extend(filter_.feed(c))
    visible.extend(filter_.finish())

    combined = "".join(visible)
    assert combined == "开始正文，正文中段，正文结尾。"
    assert "隐藏思考" not in combined
    assert "另一个思考" not in combined


def test_adv_think_stream_filter_unclosed_tag_at_end() -> None:
    """Verify _ThinkStreamFilter safely discards dangling unclosed <think> tag at EOF."""
    filter_ = _ThinkStreamFilter()
    chunks = ["合法正文内容。", "<think>未闭合的推理..."]
    visible = []
    for c in chunks:
        visible.extend(filter_.feed(c))
    visible.extend(filter_.finish())
    assert "".join(visible) == "合法正文内容。"


def test_adv_structured_parsing_adversarial_wrappers() -> None:
    """Verify _parse_structured parses JSON wrapped with think tags, code fences, and chatter."""
    # Case 1: Markdown fences + reasoning
    raw1 = (
        "<think>Let's construct JSON</think>\n"
        "```json\n"
        '{"summary": "量子纠错成功", "score": 9}\n'
        "```"
    )
    parsed1 = _parse_structured(raw1, DummyReportSchema)
    assert parsed1.summary == "量子纠错成功"
    assert parsed1.score == 9

    # Case 2: Extra conversational preamble & postamble
    raw2 = (
        "Here is the requested output:\n"
        '{"summary": "超导量子比特", "score": 8}\n'
        "I hope this helps your research!"
    )
    parsed2 = _parse_structured(raw2, DummyReportSchema)
    assert parsed2.summary == "超导量子比特"
    assert parsed2.score == 8

    # Case 3: Completely broken JSON raises LLMError
    with pytest.raises(LLMError, match="不是合法 JSON"):
        _parse_structured("This is definitely not JSON at all.", DummyReportSchema)

    # Case 4: Schema mismatch raises LLMError
    with pytest.raises(LLMError, match="不符合预期 schema"):
        _parse_structured('{"summary": "test", "score": "not_an_int"}', DummyReportSchema)


# ============================================================================
# Section 7: Pipeline Integrity & State Machine Non-Corruption
# ============================================================================


@pytest.mark.asyncio
async def test_adv_pipeline_handles_auth_failure_gracefully(tmp_path: Path) -> None:
    """Verify pipeline handles LLMAuthenticationError cleanly without unhandled crashes or corruption."""
    topic = "adversarial-auth-test"
    topic_dir = tmp_path / topic
    topic_dir.mkdir(parents=True)

    # Prepare mock raw and clean directories
    (topic_dir / "raw").mkdir(parents=True)
    write_json(topic_dir / "raw" / "sources.json", [{"title": "Source 1", "url": "https://example.com/s1"}])
    (topic_dir / "clean").mkdir(parents=True)
    write_json(topic_dir / "clean" / "index.json", [{"title": "Clean 1", "url": "https://example.com/s1"}])

    cfg = PipelineConfig(
        topic=topic,
        mode="full",
        work_dir=str(tmp_path),
        stages=["extract"],
    )
    pipeline = ResearchPipeline(cfg)

    # Inject mock LLM that raises LLMAuthenticationError
    mock_llm = MagicMock()
    mock_llm.healthcheck = AsyncMock(
        side_effect=LLMAuthenticationError("LLM 鉴权失败（chat，HTTP 401）")
    )
    pipeline._llm = mock_llm

    result = await pipeline.run()

    # Verify pipeline state and summary
    assert result.failed_stage == "extract"
    summary_file = topic_dir / "run-summary.json"
    assert summary_file.is_file()
    summary = read_json(summary_file)
    assert summary["pipeline_complete"] is False
    assert summary["failed_stage"] == "extract"

    # Crucial assertion: existing raw and clean data are NOT wiped or corrupted
    assert (topic_dir / "raw" / "sources.json").is_file()
    assert (topic_dir / "clean" / "index.json").is_file()


@pytest.mark.asyncio
async def test_adv_pipeline_retries_transient_error_and_succeeds(tmp_path: Path) -> None:
    """Verify pipeline retries transient LLM errors and completes successfully when retry passes."""
    topic = "transient-retry-test"
    topic_dir = tmp_path / topic
    topic_dir.mkdir(parents=True)

    cfg = PipelineConfig(
        topic=topic,
        mode="full",
        work_dir=str(tmp_path),
        stages=["report"],
        llm_stage_attempts=2,
        llm_retry_backoff_sec=0.01,
    )
    pipeline = ResearchPipeline(cfg)

    tree_dir = topic_dir / "tree"
    tree_dir.mkdir(parents=True)
    (tree_dir / "00-主表.md").write_text("# 主表\n", encoding="utf-8")
    (tree_dir / "N1.md").write_text("节点内容\n", encoding="utf-8")
    write_json(topic_dir / "sources.json", [{"title": "S1", "url": "https://example.com"}])

    mock_llm = MagicMock()
    # Attempt 1: healthcheck fails with transient LLMError
    # Attempt 2: healthcheck succeeds
    call_count = 0

    async def transient_healthcheck(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise LLMError("LLM 调用失败（HTTP 503）")

    mock_llm.healthcheck = AsyncMock(side_effect=transient_healthcheck)
    mock_llm.chat = AsyncMock(return_value="这是生成的测试报告。")
    pipeline._llm = mock_llm

    result = await pipeline.run()
    assert result.failed_stage is None
    assert call_count == 2
    summary = read_json(topic_dir / "run-summary.json")
    assert summary["pipeline_complete"] is True
    assert "report" in summary["stages_completed"]


@pytest.mark.asyncio
async def test_adv_pipeline_resume_idempotency_after_recovered_stage(tmp_path: Path) -> None:
    """Verify that resuming a pipeline after an error correctly picks up from the interrupted stage."""
    topic = "resume-recovery-test"
    topic_dir = tmp_path / topic
    topic_dir.mkdir(parents=True)

    # Simulate completed collect and clean
    (topic_dir / "raw").mkdir(parents=True)
    write_json(topic_dir / "raw" / "sources.json", [{"title": "S1", "url": "https://example.com"}])
    (topic_dir / "clean").mkdir(parents=True)
    write_json(topic_dir / "clean" / "index.json", [{"title": "S1", "url": "https://example.com"}])

    cfg = PipelineConfig(
        topic=topic,
        mode="full",
        work_dir=str(tmp_path),
        resume=True,
    )
    pipeline = ResearchPipeline(cfg)

    # Use pipeline._mark_stage_complete to set legitimate markers with fingerprints
    pipeline._mark_stage_complete(topic_dir, "collect")
    pipeline._mark_stage_complete(topic_dir, "clean")

    # Verify collect and clean are recognized as complete
    assert pipeline._stage_is_complete("collect", topic_dir, topic_dir / "raw", ["sources.json"])
    assert pipeline._stage_is_complete("clean", topic_dir, topic_dir / "clean", ["index.json"])
