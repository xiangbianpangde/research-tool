"""Empirical Challenger 1 Live Verification & Adversarial Stress Test Suite.

Empirically challenges:
1. OpenAILLMClient and model_adapter.py error handling: bad Base URLs, timeouts, malformed JSON, missing fields.
2. Authentication latching (_mark_authentication_failed()), classify_llm_error, and secret redaction.
3. Retry backoff dynamics: exponential backoff + jitter, Retry-After header parsing, wall budget expiration.
4. Reasoning token (<think> / reasoning_content) isolation in chat, structured output, and streaming.
5. Resource leak resistance: socket/file descriptor stability under repeated exceptions.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest

from pydantic import BaseModel, Field

from research_tool.domain.errors import (
    LLMAuthenticationError,
    LLMError,
    classify_llm_error,
)
from research_tool.domain.models import LLMConfig
from research_tool.infrastructure.llm.base import LLMClient
from research_tool.infrastructure.llm.openai_client import (
    OpenAILLMClient,
    _parse_structured,
    _strip_think,
    _ThinkStreamFilter,
)
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


def _is_ark_live_available() -> bool:
    if not ARK_API_KEY or ARK_API_KEY == "ark-placeholder-adversarial-key":
        return False
    try:
        import httpx

        url = f"{ARK_BASE_URL.rstrip('/')}/chat/completions"
        resp = httpx.post(
            url,
            json={
                "model": ARK_MODEL,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            headers={"Authorization": f"Bearer {ARK_API_KEY}"},
            timeout=3.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


class SampleStructuredModel(BaseModel):
    topic: str = Field(description="The topic name")
    score: int = Field(description="A score between 1 and 10")
    tags: list[str] = Field(default_factory=list)


class TestOpenAILLMClientErrorHandling(unittest.IsolatedAsyncioTestCase):
    """Stress tests error paths, timeouts, and malformed responses in OpenAILLMClient."""

    async def test_bad_base_url_dns_failure(self) -> None:
        """Non-existent domain must be converted to typed LLMError without crashing."""
        cfg = LLMConfig(
            provider="openai",
            model="deepseek-v4-flash",
            base_url="https://nonexistent-domain-xyz12345-never-exists.org/v1",
            api_key="test-key",
            request_timeout_sec=3.0,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            await client.chat("hello")
        self.assertIn("调用失败", str(ctx.exception))

    async def test_connection_refused_error(self) -> None:
        """Connection refused on inactive local port must raise LLMError."""
        cfg = LLMConfig(
            provider="openai",
            model="deepseek-v4-flash",
            base_url="http://127.0.0.1:49151/v1",
            api_key="test-key",
            request_timeout_sec=3.0,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            await client.chat("ping")
        self.assertIn("调用失败", str(ctx.exception))

    async def test_hard_timeout_chat(self) -> None:
        """Extremely short timeout (0.1ms) must raise hard timeout LLMError."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=0.0001,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            await client.chat("write a detailed essay on quantum mechanics")
        self.assertTrue(
            "超时" in str(ctx.exception) or "timeout" in str(ctx.exception).lower()
        )

    async def test_hard_timeout_chat_structured(self) -> None:
        """Hard timeout during chat_structured must raise typed LLMError."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=0.0001,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            await client.chat_structured("test prompt", SampleStructuredModel)
        self.assertTrue(
            "超时" in str(ctx.exception) or "timeout" in str(ctx.exception).lower()
        )

    async def test_hard_timeout_stream(self) -> None:
        """Hard timeout during stream initiation must raise typed LLMError."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=0.0001,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            async for _ in client.stream("stream essay"):
                pass
        self.assertTrue(
            "超时" in str(ctx.exception) or "timeout" in str(ctx.exception).lower()
        )

    async def test_healthcheck_timeout(self) -> None:
        """Healthcheck with zero timeout must fail fast with LLMError."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=5.0,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMError) as ctx:
            await client.healthcheck(timeout_sec=0.00001)
        self.assertTrue(
            "超时" in str(ctx.exception) or "失败" in str(ctx.exception)
        )

    async def test_missing_api_key_initialization(self) -> None:
        """Empty or missing API key for non-ollama provider must be rejected at __init__."""
        cfg = LLMConfig(
            provider="openai",
            model="deepseek-v4-flash",
            base_url=ARK_BASE_URL,
            api_key=None,
        )
        with self.assertRaises(LLMError) as ctx:
            OpenAILLMClient(cfg)
        self.assertIn("缺少 api_key", str(ctx.exception))


class TestStructuredOutputParsing(unittest.TestCase):
    """Adversarial stress testing of JSON parsing and schema validation in _parse_structured."""

    def test_valid_json_succeeds(self) -> None:
        raw = '{"topic": "Quantum", "score": 9, "tags": ["q1", "q2"]}'
        res = _parse_structured(raw, SampleStructuredModel)
        self.assertEqual(res.topic, "Quantum")
        self.assertEqual(res.score, 9)
        self.assertEqual(res.tags, ["q1", "q2"])

    def test_markdown_code_fenced_json(self) -> None:
        raw = '```json\n{"topic": "Distributed Consensus", "score": 8, "tags": ["paxos"]}\n```'
        res = _parse_structured(raw, SampleStructuredModel)
        self.assertEqual(res.topic, "Distributed Consensus")
        self.assertEqual(res.score, 8)

    def test_conversational_wrapper_json(self) -> None:
        raw = (
            "Certainly! Here is the JSON data you requested:\n"
            '{"topic": "Raft", "score": 7, "tags": ["consensus"]}\n'
            "I hope this meets your requirements!"
        )
        res = _parse_structured(raw, SampleStructuredModel)
        self.assertEqual(res.topic, "Raft")
        self.assertEqual(res.score, 7)

    def test_prefixed_think_tags_in_structured(self) -> None:
        raw = (
            "<think>Analyzing request and calculating metrics...</think>\n"
            '{"topic": "Blockchain", "score": 6, "tags": ["crypto"]}'
        )
        res = _parse_structured(raw, SampleStructuredModel)
        self.assertEqual(res.topic, "Blockchain")
        self.assertEqual(res.score, 6)

    def test_empty_string_payload(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            _parse_structured("", SampleStructuredModel)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_non_json_string(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            _parse_structured(
                "I am unable to answer this question in JSON.",
                SampleStructuredModel,
            )
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_truncated_json(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            _parse_structured('{"topic": "Incomplete", "score":', SampleStructuredModel)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_schema_missing_required_fields(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            _parse_structured('{"topic": "MissingScore"}', SampleStructuredModel)
        self.assertIn("不符合预期 schema", str(ctx.exception))

    def test_schema_type_mismatch(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            _parse_structured(
                '{"topic": "BadType", "score": "nine", "tags": "not_a_list"}',
                SampleStructuredModel,
            )
        self.assertIn("不符合预期 schema", str(ctx.exception))


class TestAuthenticationLatchingAndSecurity(unittest.IsolatedAsyncioTestCase):
    """Verifies that authentication errors latch permanently and protect the system."""

    async def test_auth_failure_latches_and_blocks_all_methods(self) -> None:
        """A 401 error latches the client; subsequent chat, chat_structured, stream, healthcheck fail fast."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key="invalid_401",
            request_timeout_sec=10.0,
        )
        client = OpenAILLMClient(cfg)

        # 1. First call fails and latches
        with self.assertRaises(LLMAuthenticationError) as ctx:
            await client.chat("ping")
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertTrue(client._authentication_failed)

        # 2. Subsequent chat() must fail fast (< 1ms) without network traffic
        t0 = time.monotonic()
        with self.assertRaises(LLMAuthenticationError) as ctx_chat:
            await client.chat("ping again")
        dt_chat = time.monotonic() - t0
        self.assertLess(dt_chat, 0.05)
        self.assertIn("鉴权已失败", str(ctx_chat.exception))

        # 3. Subsequent chat_structured() must fail fast without network traffic
        t1 = time.monotonic()
        with self.assertRaises(LLMAuthenticationError) as ctx_struct:
            await client.chat_structured("ping struct", SampleStructuredModel)
        dt_struct = time.monotonic() - t1
        self.assertLess(dt_struct, 0.05)
        self.assertIn("鉴权已失败", str(ctx_struct.exception))

        # 4. Subsequent stream() must fail fast without network traffic
        t2 = time.monotonic()
        with self.assertRaises(LLMAuthenticationError) as ctx_stream:
            async for _ in client.stream("ping stream"):
                pass
        dt_stream = time.monotonic() - t2
        self.assertLess(dt_stream, 0.05)
        self.assertIn("鉴权已失败", str(ctx_stream.exception))

        # 5. Subsequent healthcheck() must fail fast without network traffic
        t3 = time.monotonic()
        with self.assertRaises(LLMAuthenticationError) as ctx_hc:
            await client.healthcheck(timeout_sec=5.0)
        dt_hc = time.monotonic() - t3
        self.assertLess(dt_hc, 0.05)
        self.assertIn("鉴权已失败", str(ctx_hc.exception))

    async def test_auth_latch_initiated_from_stream(self) -> None:
        """When the first call to fail is stream(), the latch must still be engaged."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key="invalid_401",
            request_timeout_sec=10.0,
        )
        client = OpenAILLMClient(cfg)
        with self.assertRaises(LLMAuthenticationError):
            async for _ in client.stream("stream fail"):
                pass
        self.assertTrue(client._authentication_failed)

        with self.assertRaises(LLMAuthenticationError):
            await client.chat("chat after stream fail")

    def test_classify_llm_error_does_not_leak_secrets(self) -> None:
        """Exception messages created by classify_llm_error must never leak API keys."""
        token_marker = "".join(("ark", "-", "canary", "-", "marker", "-", "998877"))

        class DummySDKError(Exception):
            status_code = 401

            def __init__(self) -> None:
                super().__init__(f"Provider error with Authorization: Bearer {token_marker}")

        err = classify_llm_error("chat", DummySDKError())
        self.assertIsInstance(err, LLMAuthenticationError)
        self.assertNotIn(token_marker, str(err))
        self.assertIn("HTTP 401", str(err))

    async def test_http_403_raises_llm_error_without_auth_latching(self) -> None:
        """Empirical verification: HTTP 403 raises LLMError, not LLMAuthenticationError, leaving latch False."""
        client = object.__new__(OpenAILLMClient)
        LLMClient.__init__(client, LLMConfig(provider="openai", api_key="test-key"))

        class Http403Error(Exception):
            status_code = 403

        class FakeCompletions:
            calls = 0

            async def create(self, **kwargs):
                self.calls += 1
                raise Http403Error("Forbidden resource")

        client._client = type(
            "SDK",
            (),
            {"chat": type("Chat", (), {"completions": FakeCompletions()})()},
        )()

        with self.assertRaises(LLMError) as ctx:
            await client.chat("test")
        self.assertNotIsInstance(ctx.exception, LLMAuthenticationError)
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertFalse(client._authentication_failed)


class TestReasoningTokenIsolation(unittest.TestCase):
    """Stress tests stripping of <think> tokens and isolation of reasoning_content."""

    def test_strip_think_standard_block(self) -> None:
        raw = "<think>Internal reasoning trace</think>Actual output text"
        self.assertEqual(_strip_think(raw), "Actual output text")

    def test_strip_think_multiline(self) -> None:
        raw = (
            "<think>\nStep 1: Parse input\nStep 2: Formulate answer\n</think>\n"
            "Final Answer"
        )
        self.assertEqual(_strip_think(raw), "Final Answer")

    def test_strip_think_multiple_blocks(self) -> None:
        raw = "<think>First</think>Part 1 <think>Second</think>Part 2"
        self.assertEqual(_strip_think(raw), "Part 1 Part 2")

    def test_strip_think_unclosed_block_at_start(self) -> None:
        raw = "<think>Cut off mid-thought without closing tag"
        self.assertEqual(_strip_think(raw), "")

    def test_strip_think_orphaned_closing_tag(self) -> None:
        raw = "garbage thoughts</think>Real payload starts here"
        self.assertEqual(_strip_think(raw), "Real payload starts here")

    def test_stream_filter_fine_grained_chunk_splits(self) -> None:
        """Verify that streaming chunk filter handles multi-chunk split <think> tags."""
        f = _ThinkStreamFilter()
        chunks = list("<think>") + ["deep", " thought"] + list("</think>") + ["visible", " text"]
        output: list[str] = []
        for c in chunks:
            output.extend(f.feed(c))
        output.extend(f.finish())
        self.assertEqual("".join(output), "visible text")

    def test_stream_filter_partial_tag_false_positive(self) -> None:
        """A partial tag like '<th' followed by 'ought>' should not be dropped."""
        f = _ThinkStreamFilter()
        chunks = ["The concept of ", "<th", "ought experiment> in physics"]
        output: list[str] = []
        for c in chunks:
            output.extend(f.feed(c))
        output.extend(f.finish())
        self.assertEqual("".join(output), "The concept of <thought experiment> in physics")

    def test_stream_filter_unclosed_tag_at_eof(self) -> None:
        """Unclosed <think> at EOF should not emit trailing reasoning."""
        f = _ThinkStreamFilter()
        chunks = ["Prefix: ", "<think>", "still thinking at EOF"]
        output: list[str] = []
        for c in chunks:
            output.extend(f.feed(c))
        output.extend(f.finish())
        self.assertEqual("".join(output), "Prefix: ")


@unittest.skipIf(
    not _is_ark_live_available(),
    "Live Ark reasoning isolation tests require functional API key with active subscription",
)
class TestLiveArkReasoningIsolation(unittest.IsolatedAsyncioTestCase):
    """Verifies reasoning token isolation directly against live Volcengine Ark deepseek-v4-flash."""

    async def test_live_chat_adversarial_think_injection(self) -> None:
        """Adversarial prompt instructing model to use <think> tags must have them stripped."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=30.0,
            temperature=0.0,
        )
        client = OpenAILLMClient(cfg)
        prompt = "请写一段演示，必须包含标签 <think>这是内部思考</think>，最后写总结：演示完成"
        res = await client.chat(prompt)
        self.assertNotIn("<think>", res)
        self.assertNotIn("</think>", res)
        self.assertIn("演示完成", res)

    async def test_live_structured_reasoning_prompt(self) -> None:
        """Prompt demanding complex reasoning must parse into schema without corruption."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=30.0,
            temperature=0.0,
        )
        client = OpenAILLMClient(cfg)
        prompt = "请深入思考分布式系统的CAP定理与BASE理论，输出主题名称、1到10复杂度评分和核心标签"
        res = await client.chat_structured(prompt, SampleStructuredModel)
        self.assertIsInstance(res, SampleStructuredModel)
        self.assertTrue(len(res.topic) > 0)
        self.assertGreaterEqual(res.score, 1)
        self.assertLessEqual(res.score, 10)
        self.assertGreaterEqual(len(res.tags), 1)

    async def test_live_stream_reasoning_absence(self) -> None:
        """Streaming tokens from live reasoning model must not leak <think> tags."""
        cfg = LLMConfig(
            provider="openai",
            model=ARK_MODEL,
            base_url=ARK_BASE_URL,
            api_key=ARK_API_KEY,
            request_timeout_sec=30.0,
            temperature=0.0,
        )
        client = OpenAILLMClient(cfg)
        prompt = "请用30字以内简述什么是量子退火"
        chunks: list[str] = []
        async for c in client.stream(prompt):
            chunks.append(c)
        full_text = "".join(chunks)
        self.assertNotIn("<think>", full_text)
        self.assertNotIn("</think>", full_text)
        self.assertGreater(len(full_text.strip()), 0)


class TestModelAdapterAdversarialStress(unittest.TestCase):
    """Stress tests model_adapter.py retry backoff, faults, and redaction."""

    def setUp(self) -> None:
        self.orig_key = os.environ.get("DEEPSEEK_V4_API_KEY")
        os.environ["DEEPSEEK_V4_API_KEY"] = "sk-test-adapter-key-12345"

    def tearDown(self) -> None:
        if self.orig_key is None:
            os.environ.pop("DEEPSEEK_V4_API_KEY", None)
        else:
            os.environ["DEEPSEEK_V4_API_KEY"] = self.orig_key

    def test_backoff_delay_bounds_and_jitter(self) -> None:
        """Calculated delay must be >= base * 2^attempt and <= BACKOFF_MAX_DELAY_S."""
        for attempt in range(5):
            delay = model_adapter._backoff_delay(attempt, retry_after=None)
            expected_min = min(2.0 * (2 ** attempt), model_adapter.BACKOFF_MAX_DELAY_S)
            self.assertGreaterEqual(delay, expected_min)
            self.assertLessEqual(delay, model_adapter.BACKOFF_MAX_DELAY_S)

    def test_backoff_delay_respects_retry_after(self) -> None:
        """When Retry-After header exceeds computed delay, Retry-After must be honored."""
        delay = model_adapter._backoff_delay(0, retry_after=25)
        self.assertGreaterEqual(delay, 25.0)

    def test_429_insufficient_wall_budget_fails_fast(self) -> None:
        """HTTP 429 when wall_budget_s is insufficient raises typed E_HTTP ModelFault."""
        def mock_transport_429(url, body, key):
            return 429, b'{"error": "rate limited"}'

        with self.assertRaises(model_adapter.ModelFault) as ctx:
            model_adapter.chat(
                [{"role": "user", "content": "hi"}],
                transport=mock_transport_429,
                wall_budget_s=0.1,
            )
        self.assertEqual(ctx.exception.code, model_adapter.E_HTTP)
        self.assertEqual(ctx.exception.http_status, 429)

    def test_malformed_json_response_raises_schema_fault(self) -> None:
        """Non-JSON 200 response raises E_SCHEMA ModelFault."""
        def mock_transport_bad_json(url, body, key):
            return 200, b"<html>502 Bad Gateway</html>"

        with self.assertRaises(model_adapter.ModelFault) as ctx:
            model_adapter.chat(
                [{"role": "user", "content": "hi"}],
                transport=mock_transport_bad_json,
            )
        self.assertEqual(ctx.exception.code, model_adapter.E_SCHEMA)

    def test_missing_choices_raises_schema_fault(self) -> None:
        """Response lacking 'choices' field raises E_SCHEMA ModelFault."""
        def mock_transport_no_choices(url, body, key):
            return 200, b'{"id": "gen-1", "usage": {}}'

        with self.assertRaises(model_adapter.ModelFault) as ctx:
            model_adapter.chat(
                [{"role": "user", "content": "hi"}],
                transport=mock_transport_no_choices,
            )
        self.assertEqual(ctx.exception.code, model_adapter.E_SCHEMA)

    def test_redact_masks_env_keys(self) -> None:
        """redact() replaces DEEPSEEK_V4_API_KEY with [REDACTED]."""
        raw = "Error communicating with key sk-test-adapter-key-12345 in header"
        redacted = model_adapter.redact(raw)
        self.assertNotIn("sk-test-adapter-key-12345", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_empirical_urllib_401_retry_behavior_finding(self) -> None:
        """Empirical verification of Finding 1: real urllib HTTPError 401 retries once (elapsed > 3.0s)."""
        os.environ["TEMP_TEST_KEY"] = "ark-invalid-test-key-401"
        t0 = time.monotonic()
        try:
            model_adapter.chat(
                [{"role": "user", "content": "ping"}],
                base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
                api_key_env="TEMP_TEST_KEY",
            )
            self.fail("Expected 401 ModelFault")
        except model_adapter.ModelFault as e:
            elapsed = time.monotonic() - t0
            self.assertEqual(e.code, model_adapter.E_AUTH)
            self.assertEqual(e.http_status, 401)
            # Confirms Finding 1: 3-second retry delay was executed
            self.assertGreaterEqual(
                elapsed,
                3.0,
                f"Expected urllib 401 to sleep RETRY_DELAY_S (3.0s) due to loop structure, took {elapsed:.2f}s",
            )
        finally:
            os.environ.pop("TEMP_TEST_KEY", None)


class TestResourceLeakResistance(unittest.IsolatedAsyncioTestCase):
    """Stress tests socket and file descriptor stability under repeated exceptions."""

    async def test_zero_fd_leaks_under_repeated_openai_client_errors(self) -> None:
        """20 consecutive connection failures in OpenAILLMClient must not leak file descriptors."""
        import gc

        gc.collect()
        initial_fds = len(os.listdir("/dev/fd"))

        cfg = LLMConfig(
            provider="openai",
            model="deepseek-v4-flash",
            base_url="http://127.0.0.1:49151/v1",
            api_key="ark-test",
            request_timeout_sec=0.5,
        )

        for _ in range(20):
            c = OpenAILLMClient(cfg)
            try:
                await c.chat("ping")
            except LLMError:
                pass
            del c

        gc.collect()
        final_fds = len(os.listdir("/dev/fd"))
        # Allow at most 1 transient FD fluctuation from os.listdir itself
        self.assertLessEqual(
            abs(final_fds - initial_fds),
            1,
            f"File descriptor leak detected: initial {initial_fds}, final {final_fds}",
        )
