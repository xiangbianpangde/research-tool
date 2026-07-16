"""Markdown 学术翻译（英→中），走统一的 LLMClient。

移植自 pdf2zh 的 translate_md.py（分块策略 + 学术翻译 system prompt），
但改为复用 research-tool 的 LLMClient，保持模型无关、与主管道同源配置。
"""

from __future__ import annotations

import asyncio
import logging

from ..domain.errors import LLMAuthenticationError
from ..infrastructure.llm.base import LLMClient, gather_fail_fast

logger = logging.getLogger(__name__)

# 与 pdf2zh 一致的学术翻译规则，保证公式/引用/代码/图片不被破坏
TRANSLATE_SYSTEM = """你是专业的学术论文翻译助手。将用户给出的英文 Markdown 翻译成中文，
严格遵守以下规则：

1. 保留所有 Markdown 语法：标题（#、##）、列表、加粗、斜体、表格、代码块、引用块。
2. 行内公式 $...$ 和块级公式 $$...$$ 保持原样，不翻译公式内部任何字符。
3. 图片引用 ![](images/...) 与 HTML <img> 保持原样。
4. 代码块 ```lang ... ``` 保持原样，注释也不翻译。
5. 文献引用编号、URL、专有名词（人名、机构、模型名、数据集名）保持英文；
首次出现时可在括号内附中文译名。
6. 表格内容翻译，表格分隔符（| --- |）保持原样。
7. 输出只包含翻译后的 Markdown 正文，不要添加"以下是翻译"之类的说明、前言或后记。
"""


def chunk_markdown(md: str, max_chars: int = 3000) -> list[str]:
    """按段落分块，每块不超过 max_chars，优先在空行处切（同 pdf2zh）。"""
    paragraphs = md.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p) + 2
        if current_len + p_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [p]
            current_len = p_len
        else:
            current.append(p)
            current_len += p_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def translate_markdown(
    md: str,
    llm: LLMClient,
    *,
    chunk_size: int = 3000,
    concurrency: int = 8,
) -> str:
    """把英文 Markdown 翻译为中文，分块并发，保序拼接。"""
    if not md:
        return ""
    chunks = chunk_markdown(md, chunk_size)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(chunk: str) -> str:
        async with sem:
            try:
                return await llm.chat(chunk, system=TRANSLATE_SYSTEM, temperature=0.3)
            except LLMAuthenticationError:
                raise
            except Exception as exc:  # noqa: BLE001 - 非鉴权失败保留原文
                logger.debug("翻译分块失败: %s", exc)
                return chunk

    results = await gather_fail_fast(_one(chunk) for chunk in chunks)
    return "\n\n".join(results)
