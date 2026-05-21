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

    def _load_sources(self, tree_dir: Path) -> list[dict]:
        """读 raw/sources.json，返回 [{sid, title, url}]，sid 与采集文件序号一致。"""
        sources = tree_dir.parent / "raw" / "sources.json"
        if not sources.exists():
            return []
        try:
            data = read_json(sources)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for i, s in enumerate(data, 1):
            out.append(
                {"sid": f"{i:02d}", "title": s.get("title", ""), "url": s.get("url", "")}
            )
        return out

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
        refs = self._load_sources(tree_dir)
        source_count = len(refs)
        struct = _STYLE_STRUCT.get(self.config.style, _STYLE_STRUCT["report"])
        ref_block = "\n".join(
            f"- 来源{r['sid']}：{r['title'] or '(无标题)'} — {r['url']}" for r in refs
        )

        prompt = (
            f"基于以下知识树，撰写一篇**详实**的「{self.config.style}」风格调研报告。\n"
            f"主题：{topic or '（见内容）'}\n"
            f"章节结构参考：{struct}\n"
            "硬性要求：\n"
            "1. 含摘要；正文按知识树节点逐一展开，每节点至少一节，充分展开论据与细节，"
            "不要只写一两句结论；\n"
            "2. 涉及具体论文/资料时，**说明该来源讲了什么**（研究问题、方法/模型名、"
            "关键发现与数据），并在句末用 (来源NN) 标注，让读者知道结论出自哪篇；\n"
            "3. 末尾必须有「## 参考资料」一节，逐条列出来源编号、标题与链接；\n"
            f"4. 总长度尽量充分，但不超过约 {self.config.max_length} 字符；输出 Markdown。\n\n"
            f"=== 来源清单（编号→标题→链接）===\n{ref_block}\n\n"
            f"=== 知识树主表 ===\n{main}\n\n=== 知识树分表（含各 S1 来源依据）===\n{nodes_text}"
        )
        body = await llm.chat(prompt, system=_SYSTEM)

        header = (
            f"# {topic or '调研'} — 调研报告\n\n"
            f"> 生成日期: {date.today().isoformat()}\n"
            f"> 数据来源: {source_count} 篇/个\n"
            f"> 知识节点: {node_count} 个\n\n"
        )
        markdown = header + body
        # 兜底：若模型漏写参考资料，自动补上
        if ref_block and "参考资料" not in body:
            markdown += "\n\n## 参考资料\n" + ref_block + "\n"
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
