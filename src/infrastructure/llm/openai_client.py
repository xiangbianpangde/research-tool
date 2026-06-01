"""OpenAI 兼容客户端，覆盖 OpenAI / DeepSeek / Ollama。

依据：06 §2「第一阶段提供 OpenAILLMClient（兼容 OpenAI API）」。
DeepSeek、Ollama 均提供 OpenAI 兼容端点，通过 base_url 区分。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from ...domain.errors import LLMError
from ...domain.models import LLMConfig
from .base import LLMClient

T = TypeVar("T", bound=BaseModel)


class OpenAILLMClient(LLMClient):
    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMError(
                "需要 openai 包：pip install openai"
            ) from e
        if not config.api_key and config.provider != "ollama":
            raise LLMError(
                f"provider={config.provider} 缺少 api_key，请设置对应环境变量"
            )
        self._client = AsyncOpenAI(
            api_key=config.api_key or "ollama",
            base_url=config.base_url,
        )

    def _messages(self, prompt: str, system: str | None) -> list[dict]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model=self.config.model,
                messages=self._messages(prompt, system),
                temperature=(
                    self.config.temperature if temperature is None else temperature
                ),
                max_tokens=self.config.max_tokens,
            )
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError
            raise LLMError(f"chat 调用失败: {e}") from e
        return resp.choices[0].message.content or ""

    async def chat_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
    ) -> T:
        schema_json = json.dumps(
            schema.model_json_schema(), ensure_ascii=False, indent=2
        )
        sys = (
            (system + "\n\n") if system else ""
        ) + (
            "你必须只输出一个合法 JSON 对象，不要任何解释或 markdown 代码块。"
            f"JSON 必须符合以下 schema：\n{schema_json}"
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self.config.model,
                messages=self._messages(prompt, sys),
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"chat_structured 调用失败: {e}") from e
        content = resp.choices[0].message.content or "{}"
        return _parse_structured(content, schema)

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.config.model,
                messages=self._messages(prompt, system),
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"stream 调用失败: {e}") from e


def _parse_structured(content: str, schema: type[T]) -> T:
    """容错解析：剥离可能的 ```json 包裹再解析。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"结构化输出非合法 JSON: {e}\n原文: {content[:500]}") from e
    return schema.model_validate(data)
