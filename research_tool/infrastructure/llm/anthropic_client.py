"""Anthropic 客户端（可选 provider）。

依据 06 §2 的"后续可加"扩展点。Anthropic 无 OpenAI 式 json_object 模式，
结构化输出靠 system 约束 + 容错解析。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TypeVar

import httpx
from pydantic import BaseModel

from ...domain.errors import LLMAuthenticationError, LLMError, classify_llm_error
from ...domain.models import LLMConfig
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
        # 显式超时 + 禁 SDK 重试 + 显式禁用代理（P1/P7/P8/P9/P10）：
        # - SDK 默认 600s + max_retries=2，单次 LLM 慢响应会拖 ~9 分钟才最终失败。
        # - connect 10s / read 120s / write 30s / pool 30s；max_retries=0 让超时立即失败。
        # - httpx 显式 transport(proxy=None) 强制不走任何系统代理（dotenv 会从 .env
        #   重新加载 HTTPS_PROXY，shell 层 unset 不够，必须在客户端层 hard-disable）。
        no_proxy_transport = httpx.AsyncHTTPTransport(proxy=None)
        no_proxy_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=config.connect_timeout_sec,
                read=config.read_timeout_sec,
                write=30.0,
                pool=30.0,
            ),
            transport=no_proxy_transport,
        )
        self._client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=no_proxy_client,
            max_retries=config.sdk_max_retries,
        )

    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        import asyncio

        self._raise_if_authentication_failed()
        try:
            # 硬墙：httpx read=120s 之外再兜一层，防底层 socket 僵死（worklog P7 待修项）
            resp = await asyncio.wait_for(
                self._client.messages.create(
                    model=self.config.model,
                    system=system or "",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=(
                        self.config.temperature if temperature is None else temperature
                    ),
                    max_tokens=self.config.max_tokens,
                ),
                timeout=self.config.request_timeout_sec,
            )
        except asyncio.TimeoutError as e:
            raise LLMError(
                f"chat 调用硬超时（{self.config.request_timeout_sec:g}s）"
            ) from e
        except Exception as e:  # noqa: BLE001
            error = classify_llm_error("chat", e)
            if isinstance(error, LLMAuthenticationError):
                self._mark_authentication_failed()
            raise error
        return "".join(b.text for b in resp.content if b.type == "text")

    async def chat_structured(self, prompt: str, schema: type[T], system: str | None = None) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys = (
            (system + "\n\n") if system else ""
        ) + f"只输出符合此 schema 的合法 JSON：{schema_json}"
        content = await self.chat(prompt, system=sys)
        return _parse_structured(content, schema)

    async def stream(self, prompt: str, system: str | None = None) -> AsyncIterator[str]:
        import asyncio

        self._raise_if_authentication_failed()
        try:
            async with asyncio.timeout(self.config.request_timeout_sec):
                async with self._client.messages.stream(
                    model=self.config.model,
                    system=system or "",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.config.max_tokens,
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
        except asyncio.TimeoutError as e:
            raise LLMError(
                f"stream 调用硬超时（{self.config.request_timeout_sec:g}s）"
            ) from e
        except Exception as e:  # noqa: BLE001
            error = classify_llm_error("stream", e)
            if isinstance(error, LLMAuthenticationError):
                self._mark_authentication_failed()
            raise error
