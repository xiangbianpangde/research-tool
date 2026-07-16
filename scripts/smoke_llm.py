"""用当前 research-tool 配置验证 LLM 端点；不包含或打印任何凭据。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_tool.domain.config import load_config  # noqa: E402
from research_tool.infrastructure.llm import LLMClient  # noqa: E402


async def main() -> int:
    cfg = load_config()
    print("=== 测试当前 LLM 配置 ===")
    print(f"    provider: {cfg.llm.provider}")
    print(f"    base_url: {cfg.llm.base_url}")
    print(f"    model: {cfg.llm.model}")

    llm = LLMClient.from_config(cfg.llm)
    print(f"    客户端类型: {type(llm).__name__}")

    print("\n=== healthcheck() 测试 ===")
    await llm.healthcheck()
    print("PONG OK")

    print("\n=== chat() 测试 ===")
    reply = await llm.chat("用一句话介绍 Transformer 架构。")
    print(f"回复:\n---\n{reply}\n---")

    print("\n=== stream() 测试 ===")
    chunks = []
    async for chunk in llm.stream("列出 Transformer 的三大核心组件："):
        chunks.append(chunk)
        print(chunk, end="", flush=True)
    print()
    print(f"\n共 {len(chunks)} 个 chunk, 总 {sum(len(c) for c in chunks)} 字")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
