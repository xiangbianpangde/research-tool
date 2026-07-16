"""OpenAI 兼容客户端，覆盖 OpenAI / DeepSeek / Ollama。

依据：06 §2「第一阶段提供 OpenAILLMClient（兼容 OpenAI API）」。
DeepSeek、Ollama 均提供 OpenAI 兼容端点，通过 base_url 区分。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ...domain.errors import LLMAuthenticationError, LLMError, classify_llm_error
from ...domain.models import LLMConfig
from .base import LLMClient

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

T = TypeVar("T", bound=BaseModel)


class OpenAILLMClient(LLMClient):
    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMError("需要 openai 包：pip install openai") from e
        if not config.api_key and config.provider != "ollama":
            raise LLMError(f"provider={config.provider} 缺少 api_key，请设置对应环境变量")
        # 与 anthropic_client 对齐（P10/P12）：显式超时 + 禁 SDK 重试 + 禁隐式代理。
        # dotenv 会把 HTTPS_PROXY=127.0.0.1:10809 注入环境；trust_env 默认 True 会踩死连接。
        no_proxy_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0),
            transport=httpx.AsyncHTTPTransport(proxy=None),
        )
        self._client = AsyncOpenAI(
            api_key=config.api_key or "ollama",
            base_url=config.base_url,
            http_client=no_proxy_client,
            max_retries=0,
        )

    def _messages(self, prompt: str, system: str | None) -> list[ChatCompletionMessageParam]:
        msgs: list[ChatCompletionMessageParam] = []
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
        import asyncio

        self._raise_if_authentication_failed()
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self.config.model,
                    messages=self._messages(prompt, system),
                    temperature=(
                        self.config.temperature if temperature is None else temperature
                    ),
                    max_tokens=self.config.max_tokens,
                ),
                timeout=180.0,
            )
        except asyncio.TimeoutError as e:
            raise LLMError("chat 调用硬超时（180s）") from e
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError
            error = classify_llm_error("chat", e)
            if isinstance(error, LLMAuthenticationError):
                self._mark_authentication_failed()
            raise error
        return _strip_think(resp.choices[0].message.content or "")

    async def chat_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
    ) -> T:
        import asyncio

        self._raise_if_authentication_failed()
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
        sys = ((system + "\n\n") if system else "") + (
            "你必须只输出一个合法 JSON 对象，不要任何解释或 markdown 代码块。"
            f"JSON 必须符合以下 schema：\n{schema_json}"
        )
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self.config.model,
                    messages=self._messages(prompt, sys),
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    response_format={"type": "json_object"},
                ),
                timeout=180.0,
            )
        except asyncio.TimeoutError as e:
            raise LLMError("chat_structured 调用硬超时（180s）") from e
        except Exception as e:  # noqa: BLE001
            error = classify_llm_error("chat_structured", e)
            if isinstance(error, LLMAuthenticationError):
                self._mark_authentication_failed()
            raise error
        content = resp.choices[0].message.content or "{}"
        return _parse_structured(content, schema)

    async def stream(self, prompt: str, system: str | None = None) -> AsyncIterator[str]:
        import asyncio

        self._raise_if_authentication_failed()
        think_filter = _ThinkStreamFilter()
        try:
            async with asyncio.timeout(180.0):
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
                        for visible in think_filter.feed(delta):
                            yield visible
                for visible in think_filter.finish():
                    yield visible
        except asyncio.TimeoutError as e:
            raise LLMError("stream 调用硬超时（180s）") from e
        except Exception as e:  # noqa: BLE001
            error = classify_llm_error("stream", e)
            if isinstance(error, LLMAuthenticationError):
                self._mark_authentication_failed()
            raise error


def _partial_tag_suffix(text: str, tag: str) -> int:
    return max(
        (size for size in range(1, min(len(text), len(tag) - 1) + 1) if text.endswith(tag[:size])),
        default=0,
    )


class _ThinkStreamFilter:
    """Remove reasoning blocks even when XML-like tags span SDK chunks."""

    opening = "<think>"
    closing = "</think>"

    def __init__(self) -> None:
        self.buffer = ""
        self.inside = False

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        visible: list[str] = []
        while self.buffer:
            tag = self.closing if self.inside else self.opening
            index = self.buffer.find(tag)
            if index >= 0:
                if not self.inside and index:
                    visible.append(self.buffer[:index])
                self.buffer = self.buffer[index + len(tag) :]
                self.inside = not self.inside
                continue
            keep = _partial_tag_suffix(self.buffer, tag)
            if not self.inside:
                output = self.buffer[:-keep] if keep else self.buffer
                if output:
                    visible.append(output)
            self.buffer = self.buffer[-keep:] if keep else ""
            break
        return visible

    def finish(self) -> list[str]:
        if self.inside or not self.buffer:
            return []
        pending, self.buffer = self.buffer, ""
        return [pending]


def _strip_think(text: str) -> str:
    """剥离推理模型（如 MiniMax-M3）输出里的 <think>...</think> 块。

    M3 即使在 json_object 模式下也会先输出 <think> 推理，且 chat()/report 文本里也会
    混入；统一在客户端层清理。兜底：若只有未闭合的残留 </think>，截掉它之前的全部内容。
    """
    import re

    out = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in out:  # 未配对的残留：真正的正文在最后一个 </think> 之后
        out = out.rsplit("</think>", 1)[1]
    if "<think>" in out:  # 开了 think 但被截断、没有 </think>
        out = out.split("<think>", 1)[0]
    return out.strip()


def _parse_structured(content: str, schema: type[T]) -> T:
    """容错解析：剥离 <think> 推理块 / ```json 包裹 / 多余文本后再解析。"""
    text = _strip_think(content.strip())
    # 2) 去掉 ```json ... ``` 代码块包裹
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # 3) 兜底：截取首个 { 到最后一个 } 之间的子串（应对前后残留说明文字）
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise LLMError("LLM 结构化输出不是合法 JSON") from e
        else:
            raise LLMError("LLM 结构化输出不是合法 JSON") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError("LLM 结构化输出不符合预期 schema") from e
