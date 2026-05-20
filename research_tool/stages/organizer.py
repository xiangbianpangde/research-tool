"""Stage 4: Organizer —— 构建知识树（主表 + 4-7 分表）。

依据 01 §5、方法论 阶段3、07 §5（知识树结构、节点划分原则）。
- 输入既可是 extracted/（结构化）也可是 clean/（降级路径，决策 06-3）
- LLM 先规划 4-7 个节点，再逐节点生成 S1-S4，最后合成主表
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..models import OrganizeResult, OrganizerConfig
from .base import ensure_dir, read_json, write_text

_CONTEXT_BUDGET = 12000  # 送 LLM 的证据上限（字符）
_ILLEGAL = re.compile(r'[\\/:*?"<>|\s]+')

_SYSTEM = "你是知识架构专家，擅长把零散资料组织成结构化知识树。"


class _Node(BaseModel):
    title: str
    core_question: str = ""


class _NodePlan(BaseModel):
    nodes: list[_Node] = Field(default_factory=list)
    main_thread: str = ""  # 主线（因果/递进关系）一段话


def _filename_safe(name: str) -> str:
    return _ILLEGAL.sub("-", name.strip()).strip("-") or "node"


def _load_evidence(input_dir: Path) -> tuple[str, bool]:
    """返回 (证据文本, 是否结构化)。优先 extracted/，否则读 clean 文本。"""
    ent = input_dir / "entities.json"
    if ent.exists():
        entities = read_json(ent)
        rel_path = input_dir / "relations.json"
        tri_path = input_dir / "triples.json"
        relations = read_json(rel_path) if rel_path.exists() else []
        triples = read_json(tri_path) if tri_path.exists() else []
        blob = {
            "entities": entities[:200],
            "relations": relations[:200],
            "triples": triples[:200],
        }
        return json.dumps(blob, ensure_ascii=False, indent=1)[:_CONTEXT_BUDGET], True

    # 降级：拼接 clean 文本
    pieces: list[str] = []
    total = 0
    for md in sorted(input_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        pieces.append(f"### 来源 {md.name}\n{text}")
        total += len(text)
        if total >= _CONTEXT_BUDGET:
            break
    return "\n\n".join(pieces)[:_CONTEXT_BUDGET], False


class Organizer:
    def __init__(self, config: OrganizerConfig | None = None) -> None:
        self.config = config or OrganizerConfig()

    async def run(
        self,
        input_dir: Path,
        llm: LLMClient,
        work_dir: Path | None = None,
        topic: str = "",
    ) -> OrganizeResult:
        input_dir = Path(input_dir)
        tree_dir = (
            Path(work_dir) / "tree" if work_dir is not None else input_dir.parent / "tree"
        )
        ensure_dir(tree_dir)

        evidence, structured = _load_evidence(input_dir)

        plan = await self._plan_nodes(topic, evidence, structured, llm)
        nodes = plan.nodes[: self.config.max_nodes]

        # 逐节点生成 S1-S4
        node_titles = [n.title for n in nodes]
        node_bodies = await asyncio.gather(
            *[self._node_body(i + 1, n, node_titles, evidence, llm) for i, n in enumerate(nodes)]
        )

        node_paths: list[Path] = []
        for i, (node, body) in enumerate(zip(nodes, node_bodies), 1):
            fname = f"N{i}-{_filename_safe(node.title)}.md"
            path = tree_dir / fname
            write_text(path, body)
            node_paths.append(path)

        cross_refs = {n.title: [t for t in node_titles if t != n.title] for n in nodes}
        main_table = await self._main_table(plan, node_paths, llm)
        main_path = tree_dir / "00-主表.md"
        write_text(main_path, main_table)

        return OrganizeResult(
            main_table=main_path,
            nodes=node_paths,
            cross_refs=cross_refs,
            tree_dir=tree_dir,
        )

    async def _plan_nodes(
        self, topic: str, evidence: str, structured: bool, llm: LLMClient
    ) -> _NodePlan:
        subject = topic or "下述资料所讨论的主题"
        prompt = (
            f"调研主题是：「{subject}」。\n"
            f"请基于以下{'结构化知识' if structured else '资料'}，"
            f"把该主题拆分为 {self.config.min_nodes}-{self.config.max_nodes} 个子主题节点，"
            "组织成一棵知识树。\n"
            "要求：\n"
            f"- 每个节点必须是「{subject}」本身的一个具体方面/子问题，"
            "不要把“知识树/知识体系/调研方法”当作主题；\n"
            "- 每个节点回答一个核心问题，节点间有因果或递进关系；\n"
            "- 节点标题紧扣主题内容、用问题或要点导向。\n"
            "返回 JSON：{nodes:[{title, core_question}], main_thread}。\n\n"
            f"--- 资料 ---\n{evidence}"
        )
        plan = await llm.chat_structured(prompt, _NodePlan, system=_SYSTEM)
        if not plan.nodes:
            plan.nodes = [_Node(title="概述", core_question="该主题的核心内容是什么？")]
        return plan

    async def _node_body(
        self, idx: int, node: _Node, all_titles: list[str], evidence: str, llm: LLMClient
    ) -> str:
        others = "、".join(t for t in all_titles if t != node.title) or "（无）"
        prompt = (
            f"为知识树节点撰写内容。节点：{node.title}\n"
            f"核心问题：{node.core_question}\n"
            f"其他节点：{others}\n\n"
            "严格按以下 Markdown 模板输出（保留 S1-S4 小节标题）：\n"
            f"# N{idx} {node.title}\n\n> 核心问题：{node.core_question}\n\n"
            "## S1 原始资料依据\n（引用相关实体/关系/摘录）\n\n"
            "## S2 核心观点提炼\n（3-5 条）\n\n"
            "## S3 与其他节点的关系\n\n"
            "## S4 在主线中的位置\n\n"
            f"--- 资料 ---\n{evidence}"
        )
        return await llm.chat(prompt, system=_SYSTEM)

    async def _main_table(
        self, plan: _NodePlan, node_paths: list[Path], llm: LLMClient
    ) -> str:
        index = "\n".join(
            f"- [N{i} {p.stem.split('-', 1)[-1]}]({p.name})"
            for i, p in enumerate(node_paths, 1)
        )
        node_list = "\n".join(f"- {n.title}：{n.core_question}" for n in plan.nodes)
        prompt = (
            "为知识树撰写主表（00-主表.md）。包含四部分：\n"
            "1. 节点索引（每节点一句话核心问题）\n"
            "2. 主线流程图（因果/递进，用文字或 mermaid）\n"
            "3. 跨节点交叉关联表（Markdown 表格）\n"
            "4. 来源索引提示\n\n"
            f"节点清单：\n{node_list}\n\n主线提示：{plan.main_thread}\n"
            "直接输出 Markdown。"
        )
        body = await llm.chat(prompt, system=_SYSTEM)
        return f"# 知识体系总览\n\n## 节点索引\n{index}\n\n{body}\n"


async def organize(
    extracted_dir: Path, config: OrganizerConfig, llm: LLMClient
) -> OrganizeResult:
    """模块级函数（01 §5.2 签名）。"""
    return await Organizer(config).run(extracted_dir, llm)
