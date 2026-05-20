"""MockLLMClient：测试与离线开发用，不需要 API Key。

依据 06 §2「测试友好：单元测试只需 mock LLMClient」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TypeVar

from pydantic import BaseModel

from ..models import LLMConfig
from .base import LLMClient

T = TypeVar("T", bound=BaseModel)


class MockLLMClient(LLMClient):
    """可注入固定返回或回调的假客户端。

    - chat_response: str 或 (prompt)->str
    - structured_response: BaseModel 或 (prompt, schema)->BaseModel
    """

    def __init__(
        self,
        chat_response: str | Callable[[str], str] = "MOCK",
        structured_response=None,
        config: LLMConfig | None = None,
    ) -> None:
        super().__init__(config or LLMConfig(provider="openai", api_key="mock"))
        self._chat_response = chat_response
        self._structured_response = structured_response
        self.calls: list[dict] = []

    async def chat(self, prompt, system=None, temperature=None) -> str:
        self.calls.append({"kind": "chat", "prompt": prompt, "system": system})
        if callable(self._chat_response):
            return self._chat_response(prompt)
        return self._chat_response

    async def chat_structured(self, prompt, schema: type[T], system=None) -> T:
        self.calls.append({"kind": "structured", "prompt": prompt, "schema": schema})
        resp = self._structured_response
        if callable(resp):
            return resp(prompt, schema)
        if isinstance(resp, schema):
            return resp
        # 缺省：构造空实例（要求 schema 字段都有默认值）
        return schema()

    async def stream(self, prompt, system=None) -> AsyncIterator[str]:
        text = await self.chat(prompt, system)
        for token in text.split():
            yield token + " "
