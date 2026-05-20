"""Anthropic 客户端（可选 provider）。

依据 06 §2 的"后续可加"扩展点。Anthropic 无 OpenAI 式 json_object 模式，
结构化输出靠 system 约束 + 容错解析。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from ..errors import LLMError
from ..models import LLMConfig
from .base import LLMClient
from .openai_client import _parse_structured

T = TypeVar("T", bound=BaseModel)


class AnthropicLLMClient(LLMClient):
    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("需要 anthropic 包：pip install anthropic") from e
        if not config.api_key:
            raise LLMError("anthropic provider 缺少 api_key（ANTHROPIC_API_KEY）")
        self._client = AsyncAnthropic(api_key=config.api_key, base_url=config.base_url)

    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        try:
            resp = await self._client.messages.create(
                model=self.config.model,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                temperature=(
                    self.config.temperature if temperature is None else temperature
                ),
                max_tokens=self.config.max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"chat 调用失败: {e}") from e
        return "".join(b.text for b in resp.content if b.type == "text")

    async def chat_structured(
        self, prompt: str, schema: type[T], system: str | None = None
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys = (
            (system + "\n\n") if system else ""
        ) + f"只输出符合此 schema 的合法 JSON：{schema_json}"
        content = await self.chat(prompt, system=sys)
        return _parse_structured(content, schema)

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        try:
            async with self._client.messages.stream(
                model=self.config.model,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.config.max_tokens,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"stream 调用失败: {e}") from e
