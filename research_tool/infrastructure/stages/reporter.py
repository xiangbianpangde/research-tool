"""Stage 5: Reporter —— 合成最终报告。

依据 01 §6、方法论 阶段5（产出类型）、07 §6。
读 tree/ 全部文件 → LLM 按 style 合成 → report.md（或 html）。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from ..llm.base import LLMClient
from ...domain.models import ReporterConfig, ReportResult, SourceAudit
from .base import read_json, write_text

logger = logging.getLogger(__name__)

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

    def _read_clean_material(self, clean_dir: Path) -> str:
        """简略版直接读取 clean/，避免为一份摘要触发 extract/organize。"""
        documents: list[str] = []
        remaining = 60000
        for md in sorted(clean_dir.glob("*.md")):
            if remaining <= 0:
                break
            text = md.read_text(encoding="utf-8")
            piece = text[:remaining]
            documents.append(f"## {md.name}\n\n{piece}")
            remaining -= len(piece)
        return "\n\n---\n\n".join(documents)

    def _load_sources(self, tree_dir: Path) -> list[dict]:
        """读 raw/sources.json，返回 [{sid, title, url}]，sid 与采集文件序号一致。"""
        sources = tree_dir.parent / "raw" / "sources.json"
        if not sources.exists():
            return []
        try:
            data = read_json(sources)
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 sources.json 失败: %s", exc)
            return []
        out = []
        for i, s in enumerate(data, 1):
            out.append({"sid": f"{i:02d}", "title": s.get("title", ""), "url": s.get("url", "")})
        return out

    def _load_source_audits(self, tree_dir: Path) -> list[SourceAudit]:
        path = tree_dir.parent / "raw" / "source-audit.json"
        if not path.exists():
            return []
        try:
            payload = read_json(path)
            return [SourceAudit.model_validate(item) for item in payload.get("sources", [])]
        except (OSError, ValueError, AttributeError) as exc:
            logger.debug("读取 source-audit.json 失败: %s", exc)
            return []

    @staticmethod
    def _audit_markdown(audits: list[SourceAudit]) -> str:
        if not audits:
            return ""
        attempted = sum(1 for item in audits if item.attempted > 0)
        contributing = sum(1 for item in audits if item.retained > 0)
        lines = [
            "## 来源覆盖审计",
            "",
            f">已调用后端: {attempted} 个；有最终贡献: {contributing} 个。",
            "",
            "| 来源 | 调用 | 命中 | 失败 | 过滤 | 去重 | 抓取失败 | 保留 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        lines.extend(
            f"| {a.engine} | {a.attempted} | {a.hits} | {a.failed} | {a.filtered} | "
            f"{a.deduplicated} | {a.fetch_failed} | {a.retained} |"
            for a in sorted(audits, key=lambda item: item.engine)
        )
        return "\n".join(lines)

    async def run(
        self,
        tree_dir: Path,
        llm: LLMClient,
        topic: str = "",
        output_path: Path | None = None,
    ) -> ReportResult:
        tree_dir = Path(tree_dir)
        is_brief = tree_dir.name == "clean"
        if is_brief:
            main = ""
            nodes_text = self._read_clean_material(tree_dir)
            node_count = 0
        else:
            main, nodes_text = self._read_tree(tree_dir)
            node_count = len(list(tree_dir.glob("N*.md")))
        refs = self._load_sources(tree_dir)
        audits = self._load_source_audits(tree_dir)
        source_count = len(refs)
        struct = _STYLE_STRUCT.get(self.config.style, _STYLE_STRUCT["report"])
        ref_block = "\n".join(
            f"- 来源{r['sid']}：{r['title'] or '(无标题)'} — {r['url']}" for r in refs
        )

        material_name = "清洗后的原始资料" if is_brief else "知识树"
        detail_requirement = (
            "按主要观点归纳，突出结论、证据与不确定性；不要求构建知识树。"
            if is_brief
            else "正文按知识树节点逐一展开，每节点至少一节，充分展开论据与细节。"
        )
        prompt = (
            f"基于以下{material_name}，撰写一篇「{self.config.style}」风格调研报告。\n"
            f"主题：{topic or '（见内容）'}\n"
            f"章节结构参考：{struct}\n"
            "硬性要求：\n"
            f"1. 含摘要；{detail_requirement}\n"
            "2. 涉及具体论文/资料时，**说明该来源讲了什么**（研究问题、方法/模型名、"
            "关键发现与数据），并在句末用 (来源NN) 标注，让读者知道结论出自哪篇；\n"
            "3. 正文用 (来源NN) 标注每条结论出处；**不要自己编写参考资料列表**"
            "（系统会自动附上完整的来源清单）；\n"
            f"4. 总长度尽量充分，但不超过约 {self.config.max_length} 字符；输出 Markdown。\n\n"
            f"=== 来源清单（编号→标题→链接）===\n{ref_block}\n\n"
            f"=== 主表 ===\n{main}\n\n=== {material_name} ===\n{nodes_text}"
        )
        body = await llm.chat(prompt, system=_SYSTEM)

        header = (
            f"# {topic or '调研'} — 调研报告\n\n"
            f"> 生成日期: {date.today().isoformat()}\n"
            f"> 报告版本: {'简略版' if is_brief else '全量版'}\n"
            f"> 数据来源: {source_count} 篇/个\n"
        )
        if not is_brief:
            header += f"> 知识节点: {node_count} 个\n"
        header += "\n"
        # 去掉模型可能自行写的(常不全的)参考资料，统一用程序生成的权威完整列表
        body = re.split(r"\n#{1,6}\s*参考资料", body)[0].rstrip()
        audit_block = self._audit_markdown(audits)
        # 审计块放在模型正文之前，确保长报告截断时仍可见。
        markdown = header
        if audit_block:
            markdown += audit_block + "\n\n"
        markdown += body
        if ref_block:
            markdown += "\n\n## 参考资料\n" + ref_block + "\n"
        if len(markdown) > self.config.max_length:
            markdown = markdown[: self.config.max_length].rstrip() + "\n\n…（已截断）"

        if output_path is not None:
            out = Path(output_path)
        else:
            out = tree_dir.parent / ("report.html" if self.config.format == "html" else "report.md")

        content = (
            _md_to_html(markdown, topic or "调研报告") if self.config.format == "html" else markdown
        )
        write_text(out, content)

        return ReportResult(
            report_path=out,
            word_count=_count_words(markdown),
            source_count=source_count,
        )


async def report(tree_dir: Path, config: ReporterConfig, llm: LLMClient) -> ReportResult:
    """模块级函数（01 §6.2 签名）。"""
    return await Reporter(config).run(tree_dir, llm)
