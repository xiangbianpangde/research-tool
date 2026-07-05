"""Deepseek LLM 客户端（V1.1 M-006 VideoIngest）。

设计依据：[DD-001:M-006 LLM 客户端]（deepseek-v4-flash 模型）
- 适配器模式：继承 LLMClient，复用 OpenAILLMClient 的 OpenAI 协议实现
- 默认模型：deepseek-v4-flash（NFR5 中文优先）
- 异步 API：chat / chat_structured / stream 与 LLMClient 一致
- 视频场景便捷方法：summarize_transcript / extract_chapters
- 失败时通过 LLMError 抛出，由 M-010 错误码字典 E_LLM_001 统一登记

环境：
- DEEPSEEK_API_KEY：必填（或通过 LLMConfig.api_key 注入）
- DEEPSEEK_BASE_URL：可选（默认 https://api.deepseek.com/v1）
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel

from ...domain.errors import LLMError
from ...domain.models import LLMConfig
from .base import LLMClient

T = TypeVar("T", bound=BaseModel)

# Deepseek 默认端点（OpenAI 兼容）
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
# V1.1 任务默认模型（按任务说明：deepseek-v4-flash）
_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"

# 视频转写稿总结 prompt（中文优先，符合 NFR5）
_TRANSCRIPT_SUMMARIZE_PROMPT_ZH = """你是一名严谨的视频内容分析师。
请根据以下视频转写稿，输出结构化总结：

要求：
1. front_matter 字段名必须以 `video_` 开头
   （如 video_title / video_duration / video_chapters / video_tags）。
2. body 用 Markdown 撰写，分章节（H2），每章节下列要点（无序列表）。
3. chapters 为时间戳-标题列表（mm:ss 格式），如未明确时间则按内容推断。
4. 全文使用中文输出。

转写稿：
{transcript}
"""

_TRANSCRIPT_SUMMARIZE_PROMPT_EN = """You are a rigorous video content analyst.
Summarize the following video transcript with:

1. front_matter keys MUST start with `video_` prefix.
2. body in Markdown, sections (H2) with bullet points.
3. chapters: list of (mm:ss, title).
4. Output in English.

Transcript:
{transcript}
"""


class DeepseekClient(LLMClient):
    """Deepseek 异步客户端（OpenAI 兼容协议）。

    优先用 deepseek-v4-flash（V1.1 任务默认）。如需切到其他 Deepseek 模型，
    通过 `LLMConfig.model` 或环境变量 `DEEPSEEK_MODEL` 覆盖。
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        if config is None:
            config = LLMConfig(
                provider="deepseek",
                model=os.environ.get("DEEPSEEK_MODEL", _DEEPSEEK_DEFAULT_MODEL),
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url=os.environ.get("DEEPSEEK_BASE_URL", _DEEPSEEK_DEFAULT_BASE_URL),
            )
        else:
            # 强制 provider=deepseek 并补齐默认 base_url
            config = config.model_copy(
                update={
                    "provider": "deepseek",
                    "base_url": config.base_url or _DEEPSEEK_DEFAULT_BASE_URL,
                    "model": config.model or _DEEPSEEK_DEFAULT_MODEL,
                }
            )
        super().__init__(config)
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise LLMError(
                "需要 openai 包：pip install openai"
            ) from e
        if not config.api_key:
            raise LLMError(
                "Deepseek 缺少 api_key：请设置 DEEPSEEK_API_KEY 或 LLMConfig.api_key"
            )
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    # ---- 核心 LLMClient 接口（与基类契约一致） ---------------------------- #

    def _messages(self, prompt: str, system: str | None) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
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
        """单轮对话，返回纯文本。"""
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
            raise LLMError(
                f"Deepseek chat 失败: model={self.config.model}, err={e}"
            ) from e
        return resp.choices[0].message.content or ""

    async def chat_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
    ) -> T:
        """结构化输出：JSON object → Pydantic 模型。"""
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
            raise LLMError(
                f"Deepseek chat_structured 失败: model={self.config.model}, err={e}"
            ) from e
        content = resp.choices[0].message.content or "{}"
        return _parse_structured(content, schema)

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """流式返回文本增量。"""
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
            raise LLMError(
                f"Deepseek stream 失败: model={self.config.model}, err={e}"
            ) from e

    # ---- 视频场景便捷方法（V1.1 扩展） ---------------------------------- #

    async def summarize_transcript(
        self,
        transcript: str,
        *,
        language: str = "zh",
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        """对视频转写稿做结构化总结，返回 {front_matter, body, raw}。

        Args:
            transcript: 视频转写文本（已被 faster-whisper 转写）
            language: 输出语言（"zh"/"en"/"ja"）
            max_chars: 输入截断上限（避免超 token 限额）

        Returns:
            dict 包含:
                - front_matter (dict): video_ 前缀字段
                - body (str): Markdown 主体
                - raw (str): LLM 原始输出
                - model (str): 实际使用的模型名
        """
        truncated = _truncate_text(transcript, max_chars)
        if language == "zh":
            prompt = _TRANSCRIPT_SUMMARIZE_PROMPT_ZH.format(transcript=truncated)
        elif language == "ja":
            # 日文复用中文模板（提示词已用 NFR5 默认中文）
            prompt = _TRANSCRIPT_SUMMARIZE_PROMPT_ZH.format(transcript=truncated)
        else:
            prompt = _TRANSCRIPT_SUMMARIZE_PROMPT_EN.format(transcript=truncated)

        raw = await self.chat(prompt, temperature=0.3)
        front_matter, body = _parse_summary(raw)
        return {
            "front_matter": front_matter,
            "body": body,
            "raw": raw,
            "model": self.config.model,
        }

    async def extract_chapters(
        self,
        transcript: str,
        *,
        language: str = "zh",
    ) -> list[dict[str, str]]:
        """从转写稿抽取章节列表 [{ts, title}, ...]。

        Args:
            transcript: 视频转写文本
            language: 输出语言

        Returns:
            章节列表，失败时返回空列表（不抛错，由调用方判定）
        """
        from pydantic import BaseModel  # 局部 import 避免循环

        class _ChapterList(BaseModel):
            chapters: list[dict[str, str]]

        if language == "zh":
            sys_prompt = "你只输出 JSON，章节标题用中文。"
        else:
            sys_prompt = "Output JSON only, chapter titles in English."

        prompt = (
            f"从以下视频转写稿中抽取 3-7 个章节，按时间顺序排列。"
            f"每章节包含 ts（mm:ss 格式）和 title。\n\n{transcript[:20_000]}"
        )
        try:
            result = await self.chat_structured(
                prompt, _ChapterList, system=sys_prompt
            )
            return result.chapters
        except LLMError:
            return []


# ---- 工具函数（不导出） ------------------------------------------------- #


def _parse_structured(content: str, schema: type[T]) -> T:
    """容错解析：剥离可能的 ```json 包裹再解析。"""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(
            f"Deepseek 结构化输出非合法 JSON: {e}\n原文: {content[:500]}"
        ) from e
    return schema.model_validate(data)


def _truncate_text(text: str, max_chars: int) -> str:
    """粗估截断（中英文 1 字符 ≈ 1 token）。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n...[truncated]"


def _parse_summary(raw: str) -> tuple[dict[str, Any], str]:
    """从 LLM 输出中分离 front_matter（YAML/JSON 块）与 body（Markdown）。

    容错策略：
    1. 优先尝试 ```yaml ... ``` 包裹
    2. 退到 ```json ... ```
    3. 退到首段 ```...``` 块
    4. 全无 → front_matter={}，body=raw
    """
    import re

    fm: dict[str, Any] = {}
    body = raw

    # 1) YAML front matter
    yaml_match = re.search(
        r"```(?:yaml|yml)\s*\n(.*?)\n```", raw, re.DOTALL
    )
    if yaml_match:
        try:
            import yaml  # type: ignore

            fm = yaml.safe_load(yaml_match.group(1)) or {}
            body = raw[yaml_match.end():].strip()
            return _filter_video_prefix(fm), body
        except ImportError:
            pass

    # 2) JSON front matter
    json_match = re.search(
        r"```(?:json)?\s*\n(\{.*?\})\n```", raw, re.DOTALL
    )
    if json_match:
        try:
            fm = json.loads(json_match.group(1))
            body = raw[json_match.end():].strip()
            return _filter_video_prefix(fm), body
        except json.JSONDecodeError:
            pass

    return fm, body


def _filter_video_prefix(fm: dict[str, Any]) -> dict[str, Any]:
    """只保留 video_ 前缀的字段（DD-001 IC-014 front_matter 校验）。"""
    return {k: v for k, v in fm.items() if isinstance(k, str) and k.startswith("video_")}


__all__ = ["DeepseekClient"]
