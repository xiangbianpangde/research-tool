"""LLMClient 抽象接口与工厂。

依据：01-核心引擎设计.md §8、03-Python库接口设计.md §4、06-关键设计决策.md §2。
所有 Stage 只依赖此抽象，不直接 import 任何 LLM SDK。
"""

from __future__ import annotations

import abc
import asyncio
from collections.abc import AsyncIterator, Awaitable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from ...domain.errors import LLMAuthenticationError, LLMError
from ...domain.models import LLMConfig, Provider

if TYPE_CHECKING:
    from ...domain.config import load_config  # noqa: F401

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


async def gather_fail_fast(awaitables: Iterable[Awaitable[R]]) -> list[R]:
    """首个异常即取消并等待所有兄弟任务，避免 401 后仍有请求在飞。

    extractor 等“单块容错”场景必须在任务内部吞掉非鉴权异常，再交给本函数；
    不要把本函数改成 return_exceptions=True，否则 organizer/translate 的鉴权熔断会失效。
    """
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    if not tasks:
        return []
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

# 各 provider 的默认 endpoint
_DEFAULT_BASE_URL = {
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": None,
    "anthropic": None,
    "minimax": "https://api.minimaxi.com/v1",
}


class LLMClient(abc.ABC):
    """LLM 客户端抽象基类。"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._authentication_failed = False

    # -- 核心接口 -------------------------------------------------------- #

    @abc.abstractmethod
    async def chat(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """单轮对话，返回纯文本。"""

    @abc.abstractmethod
    async def chat_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
    ) -> T:
        """让 LLM 输出 JSON 并解析为给定的 Pydantic 模型。"""

    @abc.abstractmethod
    def stream(self, prompt: str, system: str | None = None) -> AsyncIterator[str]:
        """流式返回文本增量。"""

    async def healthcheck(self, timeout_sec: float = 10.0) -> None:
        """在阶段业务请求前执行短 PONG 探针；结果异常或超时都阻止阶段启动。"""
        self._raise_if_authentication_failed()
        try:
            reply = await asyncio.wait_for(
                self.chat("只回复 PONG", temperature=0),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(f"LLM 健康检查超时（{timeout_sec:g}s）") from exc
        if reply.strip().upper() != "PONG":
            raise LLMError("LLM 健康检查失败：未返回 PONG")

    def _raise_if_authentication_failed(self) -> None:
        if self._authentication_failed:
            raise LLMAuthenticationError("LLM 鉴权已失败；拒绝继续请求")

    def _mark_authentication_failed(self) -> None:
        self._authentication_failed = True

    # -- 工厂 ------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        provider: Provider = "deepseek",
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ) -> "LLMClient":
        """显式创建客户端（03 §4 方式2）。api_key 缺省时按 provider 从环境变量兜底。"""
        model = model or _default_model(provider)
        if base_url is None:
            base_url = _DEFAULT_BASE_URL.get(provider)
        if api_key is None:
            import os

            env_name = _PROVIDER_KEY_ENV_FOR_CREATE.get(provider)
            if env_name:
                api_key = os.environ.get(env_name)
        config = LLMConfig(
            provider=provider, model=model, api_key=api_key, base_url=base_url, **kwargs
        )
        return cls.from_config(config)

    @classmethod
    def from_config(cls, config: LLMConfig) -> "LLMClient":
        """根据 LLMConfig.provider 选择具体实现。"""
        provider = config.provider
        if config.base_url is None and provider in _DEFAULT_BASE_URL:
            config = config.model_copy(update={"base_url": _DEFAULT_BASE_URL[provider]})
        if provider in ("openai", "deepseek", "ollama", "minimax"):
            from .openai_client import OpenAILLMClient

            return OpenAILLMClient(config)
        if provider == "anthropic":
            from .anthropic_client import AnthropicLLMClient

            return AnthropicLLMClient(config)
        raise ValueError(f"不支持的 LLM provider: {provider}")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "LLMClient":
        """从 YAML 配置创建（03 §4 方式3）。"""
        from ...domain.config import load_config

        return cls.from_config(load_config(path).llm)

    @classmethod
    def from_config_file(cls, path: str | Path | None = None) -> "LLMClient":
        """从环境/默认配置文件创建（03 §4 方式1，即 from_config()）。"""
        from ...domain.config import load_config

        return cls.from_config(load_config(path).llm)


_PROVIDER_KEY_ENV_FOR_CREATE = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


def _default_model(provider: str) -> str:
    return {
        "deepseek": "deepseek-chat",
        "openai": "gpt-4o-mini",
        "ollama": "llama3",
        "anthropic": "claude-sonnet-4-6",
        "minimax": "MiniMax-M3",
    }.get(provider, "deepseek-chat")
