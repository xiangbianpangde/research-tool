"""LLMClient 抽象接口与工厂。

依据：01-核心引擎设计.md §8、03-Python库接口设计.md §4、06-关键设计决策.md §2。
所有 Stage 只依赖此抽象，不直接 import 任何 LLM SDK。
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from ..models import LLMConfig

if TYPE_CHECKING:
    from ..config import load_config  # noqa: F401

T = TypeVar("T", bound=BaseModel)

# 各 provider 的默认 endpoint
_DEFAULT_BASE_URL = {
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": None,
    "anthropic": None,
}


class LLMClient(abc.ABC):
    """LLM 客户端抽象基类。"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

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
    def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """流式返回文本增量。"""

    # -- 工厂 ------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        provider: str = "deepseek",
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ) -> "LLMClient":
        """显式创建客户端（03 §4 方式2）。"""
        model = model or _default_model(provider)
        if base_url is None:
            base_url = _DEFAULT_BASE_URL.get(provider)
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
        if provider in ("openai", "deepseek", "ollama"):
            from .openai_client import OpenAILLMClient

            return OpenAILLMClient(config)
        if provider == "anthropic":
            from .anthropic_client import AnthropicLLMClient

            return AnthropicLLMClient(config)
        raise ValueError(f"不支持的 LLM provider: {provider}")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "LLMClient":
        """从 YAML 配置创建（03 §4 方式3）。"""
        from ..config import load_config

        return cls.from_config(load_config(path).llm)

    @classmethod
    def from_config_file(cls, path: str | Path | None = None) -> "LLMClient":
        """从环境/默认配置文件创建（03 §4 方式1，即 from_config()）。"""
        from ..config import load_config

        return cls.from_config(load_config(path).llm)


def _default_model(provider: str) -> str:
    return {
        "deepseek": "deepseek-chat",
        "openai": "gpt-4o-mini",
        "ollama": "llama3",
        "anthropic": "claude-sonnet-4-6",
    }.get(provider, "deepseek-chat")
