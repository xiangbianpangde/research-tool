"""Stage 5: Reporter —— 合成最终报告。

依据 01 §6、方法论 阶段5（产出类型）、07 §6。
读 tree/ 全部文件 → LLM 按 style 合成 → report.md（或 html）。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..llm.base import LLMClient
from ..models import ReporterConfig, ReportResult
from .base import read_json, write_text

_SYSTEM = "你是资深研究报告撰写者，输出结构清晰、论据可追溯。"

# style → 章节结构（方法论 5.1）
_STYLE_STRUCT = {
    "report": "定义 → 来源/背景 → 核心要素 → 争议 → 应用 → 总结",
    "feasibility": "现状 → 方案选项 → 利弊 → 风险 → 建议",
    "review": "发展脉络 → 主要流派 → 关键人物/文献 → 趋势",
    "article": "引言 → 论点1 → 论点2 → … → 结论",
}


def _count_words(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z]+", text))
    return cjk + words


def _md_to_html(md: str, title: str) -> str:
    """极简 Markdown→HTML（标题/段落/列表），满足 format=html 的最小需求。"""
    lines = md.splitlines()
    html: list[str] = []
    in_list = False
    for ln in lines:
        if m := re.match(r"^(#{1,6})\s+(.*)", ln):
            if in_list:
                html.append("</ul>")
                in_list = False
            level = len(m.group(1))
            html.append(f"<h{level}>{m.group(2)}</h{level}>")
        elif m := re.match(r"^\s*[-*]\s+(.*)", ln):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{m.group(1)}</li>")
        elif ln.strip():
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(f"<p>{ln}</p>")
    if in_list:
        html.append("</ul>")
    body = "\n".join(html)
    return (
        f"<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>\n{body}\n</body></html>"
    )


class Reporter:
    def __init__(self, config: ReporterConfig | None = None) -> None:
        self.config = config or ReporterConfig()

    def _read_tree(self, tree_dir: Path) -> tuple[str, str]:
        main = ""
        main_path = tree_dir / "00-主表.md"
        if main_path.exists():
            main = main_path.read_text(encoding="utf-8")
        nodes = []
        for md in sorted(tree_dir.glob("N*.md")):
            nodes.append(md.read_text(encoding="utf-8"))
        return main, "\n\n---\n\n".join(nodes)

    def _source_count(self, tree_dir: Path) -> int:
        sources = tree_dir.parent / "raw" / "sources.json"
        if sources.exists():
            try:
                return len(read_json(sources))
            except Exception:  # noqa: BLE001
                return 0
        return 0

    async def run(
        self,
        tree_dir: Path,
        llm: LLMClient,
        topic: str = "",
        output_path: Path | None = None,
    ) -> ReportResult:
        tree_dir = Path(tree_dir)
        main, nodes_text = self._read_tree(tree_dir)
        node_count = len(list(tree_dir.glob("N*.md")))
        source_count = self._source_count(tree_dir)
        struct = _STYLE_STRUCT.get(self.config.style, _STYLE_STRUCT["report"])

        prompt = (
            f"基于以下知识树，撰写一篇「{self.config.style}」风格的调研报告。\n"
            f"主题：{topic or '（见内容）'}\n"
            f"章节结构参考：{struct}\n"
            f"要求：含摘要(3-5句)、正文按节点展开、交叉分析、结论与建议、参考资料提示；"
            f"总长度不超过约 {self.config.max_length} 字符；输出 Markdown。\n\n"
            f"=== 主表 ===\n{main}\n\n=== 分表 ===\n{nodes_text}"
        )
        body = await llm.chat(prompt, system=_SYSTEM)

        header = (
            f"# {topic or '调研'} — 调研报告\n\n"
            f"> 生成日期: {date.today().isoformat()}\n"
            f"> 数据来源: {source_count} 个网页\n"
            f"> 知识节点: {node_count} 个\n\n"
        )
        markdown = header + body
        if len(markdown) > self.config.max_length:
            markdown = markdown[: self.config.max_length].rstrip() + "\n\n…（已截断）"

        if output_path is not None:
            out = Path(output_path)
        else:
            out = tree_dir.parent / (
                "report.html" if self.config.format == "html" else "report.md"
            )

        content = (
            _md_to_html(markdown, topic or "调研报告")
            if self.config.format == "html"
            else markdown
        )
        write_text(out, content)

        return ReportResult(
            report_path=out,
            word_count=_count_words(markdown),
            source_count=source_count,
        )


async def report(
    tree_dir: Path, config: ReporterConfig, llm: LLMClient
) -> ReportResult:
    """模块级函数（01 §6.2 签名）。"""
    return await Reporter(config).run(tree_dir, llm)
