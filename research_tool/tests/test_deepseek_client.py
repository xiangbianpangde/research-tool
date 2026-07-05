"""M-006 Deepseek 客户端单元测试。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from research_tool.domain.errors import LLMError
from research_tool.domain.models import LLMConfig
from research_tool.infrastructure.llm.deepseek_client import DeepseekClient, _parse_summary, _truncate_text


class TestDeepseekClientConstruction:
    """客户端构造。"""

    def test_default_config_uses_deepseek_v4_flash(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            client = DeepseekClient()
        assert client.config.provider == "deepseek"
        assert client.config.model == "deepseek-v4-flash"
        assert client.config.api_key == "sk-test"

    def test_default_base_url(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            client = DeepseekClient()
        assert client.config.base_url == "https://api.deepseek.com/v1"

    def test_custom_config(self):
        cfg = LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="sk-custom",
        )
        client = DeepseekClient(cfg)
        assert client.config.api_key == "sk-custom"
        assert client.config.model == "deepseek-v4-flash"

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove DEEPSEEK_API_KEY explicitly
            os.environ.pop("DEEPSEEK_API_KEY", None)
            with pytest.raises(LLMError, match="api_key"):
                DeepseekClient()


class TestDeepseekClientChat:
    """chat() 单轮对话。"""

    @pytest.mark.asyncio
    async def test_chat_returns_text(self):
        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)

        # Mock 内部 _client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hello world"))]
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        out = await client.chat("hi")
        assert out == "hello world"
        client._client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_api_error_wrapped(self):
        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            side_effect=Exception("API down")
        )

        with pytest.raises(LLMError, match="Deepseek chat 失败"):
            await client.chat("hi")


class TestDeepseekClientStructured:
    """chat_structured() JSON 输出。"""

    @pytest.mark.asyncio
    async def test_chat_structured_parses_json(self):
        from pydantic import BaseModel

        class Output(BaseModel):
            name: str
            score: int

        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"name": "foo", "score": 42}'))
        ]
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        out = await client.chat_structured("prompt", Output)
        assert out.name == "foo"
        assert out.score == 42

    @pytest.mark.asyncio
    async def test_chat_structured_strips_code_block(self):
        from pydantic import BaseModel

        class Output(BaseModel):
            name: str

        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='```json\n{"name": "bar"}\n```'))
        ]
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        out = await client.chat_structured("p", Output)
        assert out.name == "bar"


class TestDeepseekSummarizeTranscript:
    """summarize_transcript() 视频场景。"""

    @pytest.mark.asyncio
    async def test_summarize_chinese(self):
        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)
        client._client = MagicMock()

        llm_output = """```yaml
video_title: 测试视频
video_duration: 600
```
正文内容：关于 AI 的演讲。
"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=llm_output))]
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.summarize_transcript("转写稿内容...")
        assert "video_title" in result["front_matter"]
        assert result["front_matter"]["video_title"] == "测试视频"
        assert "正文内容" in result["body"]
        assert result["model"] == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_summarize_truncates_long_input(self):
        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)
        client._client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="正文"))]
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        long_text = "x" * 50_000
        await client.summarize_transcript(long_text, max_chars=1000)
        # 验证 prompt 包含截断标记
        call_args = client._client.chat.completions.create.await_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "truncated" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_summarize_no_yaml_returns_empty_fm(self):
        cfg = LLMConfig(
            provider="deepseek", model="deepseek-v4-flash", api_key="sk-x"
        )
        client = DeepseekClient(cfg)
        client._client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="纯 Markdown 输出"))]
        client._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.summarize_transcript("短稿")
        assert result["front_matter"] == {}
        assert result["body"] == "纯 Markdown 输出"


class TestHelperFunctions:
    """工具函数。"""

    def test_truncate_short_unchanged(self):
        assert _truncate_text("hello", 100) == "hello"

    def test_truncate_long_marks(self):
        out = _truncate_text("x" * 100, 10)
        assert out.startswith("xxxxxxxxxx")
        assert "truncated" in out

    def test_parse_summary_yaml(self):
        text = """```yaml
video_title: t
```
body here
"""
        fm, body = _parse_summary(text)
        assert fm.get("video_title") == "t"
        assert "body here" in body

    def test_parse_summary_filters_non_video_prefix(self):
        text = """```yaml
video_title: ok
random_key: drop
```
body
"""
        fm, body = _parse_summary(text)
        assert "video_title" in fm
        assert "random_key" not in fm

    def test_parse_summary_no_block(self):
        text = "just plain text"
        fm, body = _parse_summary(text)
        assert fm == {}
        assert body == "just plain text"
