"""端到端烟囱测试：Mock LLM 注入，跑 Reporter 阶段验证管道可工作。

使用当前正式包名 ``research_tool``，避免旧 ``src`` 布局掩盖安装问题。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_tool.domain.config import load_config  # noqa: E402
from research_tool.domain.models import ReporterConfig  # noqa: E402
from research_tool.infrastructure.llm import MockLLMClient  # noqa: E402
from research_tool.infrastructure.stages import Reporter  # noqa: E402


async def main() -> int:
    topic = "Transformer 架构"
    out_dir = Path(tempfile.mkdtemp(prefix="research-smoke-"))

    cfg = load_config(
        "config.yaml",
        overrides={
            "topic": topic,
            "work_dir": str(out_dir),
            "reporter": {"format": "markdown", "style": "report", "max_length": 4000},
            "pipeline": {"resume": False, "max_backward_rounds": 0},
            "deepen": {"enabled": False},
        },
    )

    llm = MockLLMClient(
        chat_response=f"这是关于 {topic} 的示例报告段落。",
        structured_response=json.dumps(
            {
                "title": "Transformer 架构",
                "nodes": [
                    {"id": "N1", "title": "Self-Attention 机制", "summary": "核心注意力"},
                    {"id": "N2", "title": "多头注意力", "summary": "并行注意力头"},
                    {"id": "N3", "title": "位置编码", "summary": "序列位置信息"},
                ],
            },
            ensure_ascii=False,
        ),
    )

    tree_dir = out_dir / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    (tree_dir / "00-主表.md").write_text(
        "# Transformer 架构\n\n## N1 Self-Attention\n\n## N2 多头注意力\n\n## N3 位置编码\n",
        encoding="utf-8",
    )

    print(f"=== 端到端冒烟: topic={topic} ===")
    print(f"    输出目录: {out_dir}")
    print(f"    LLM Provider: {cfg.llm.provider}")
    print(f"    Reporter Style: {cfg.reporter.style}")

    report_cfg = ReporterConfig(format="markdown", style="report", max_length=4000)
    result = await Reporter(report_cfg).run(tree_dir, llm=llm, topic=topic)

    print("\n=== Reporter 完成 ===")
    print(f"    报告路径: {result.report_path}")
    print(f"    字数: {result.word_count}")
    print(f"    来源数: {result.source_count}")

    # 读取生成的报告内容确认有效
    report_text = Path(result.report_path).read_text(encoding="utf-8")
    print(f"    报告大小: {len(report_text)} 字符")
    print(f"    报告前 200 字:\n---\n{report_text[:200]}\n---")

    print(f"\n=== 产物清单 ({out_dir}) ===")
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            print(f"    {p.relative_to(out_dir)} ({p.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
