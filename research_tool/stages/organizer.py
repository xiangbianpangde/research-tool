"""Stage 4: Organizer —— 构建知识树（主表 + 4-7 分表）。

依据 01 §5、方法论 阶段3、07 §5（知识树结构、节点划分原则）。

关键设计（map-reduce）：先**逐篇**把每个来源（含完整论文）摘要为带来源标签的
要点（大文件分块摘要再合并），再用所有要点去规划节点、撰写 S1-S4。这样保证
每篇论文都被读到并可追溯到具体来源，避免早期"拼接到 N 字符就截断"导致后面的
论文整篇被忽略。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..models import OrganizeResult, OrganizerConfig
from .base import ensure_dir, read_json, write_text

_ILLEGAL = re.compile(r'[\\/:*?"<>|\s]+')
_DOC_CHUNK = 14000        # 单篇摘要时的分块大小（字符）
_MAX_CHUNKS_PER_DOC = 8   # 单篇最多摘要的块数（兜底超大文件）
_META_SRC = re.compile(r"<!--\s*source:\s*(.*?)\s*-->")
_META_TITLE = re.compile(r"<!--\s*title:\s*(.*?)\s*-->")

_SYSTEM = "你是知识架构专家，擅长把零散资料组织成结构化、可追溯的知识树。"
_SUM_SYSTEM = "你是学术资料分析专家，精准提炼资料要点并保留专有名词、方法名与数据。"


class _Node(BaseModel):
    title: str
    core_question: str = ""


class _NodePlan(BaseModel):
    nodes: list[_Node] = Field(default_factory=list)
    main_thread: str = ""  # 主线（因果/递进关系）一段话


class _Points(BaseModel):
    points: list[str] = Field(default_factory=list)


@dataclass
class _SourceDoc:
    sid: str          # "01"
    title: str
    url: str
    text: str
    digest: str = ""  # 摘要后的要点块
    points: list[str] = field(default_factory=list)


def _filename_safe(name: str) -> str:
    return _ILLEGAL.sub("-", name.strip()).strip("-") or "node"


def _strip_meta_body(text: str) -> str:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].strip().startswith("<!--") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])


def _chunk(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _load_clean_docs(input_dir: Path) -> list[_SourceDoc]:
    docs: list[_SourceDoc] = []
    for md in sorted(input_dir.glob("*.md")):
        if md.name in ("quality.json",):
            continue
        raw = md.read_text(encoding="utf-8")
        sid = md.stem.split("-", 1)[0]
        title = (_META_TITLE.search(raw) or [None, ""])[1] if _META_TITLE.search(raw) else ""
        url = (_META_SRC.search(raw) or [None, ""])[1] if _META_SRC.search(raw) else ""
        body = _strip_meta_body(raw).strip()
        if body:
            docs.append(_SourceDoc(sid=sid, title=title.strip(), url=url.strip(), text=body))
    return docs


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

        # 结构化路径（extracted/）：沿用三元组证据；否则走逐篇摘要
        ent = input_dir / "entities.json"
        if ent.exists():
            evidence = self._structured_evidence(input_dir)
            sources: list[_SourceDoc] = []
        else:
            sources = _load_clean_docs(input_dir)
            await self._summarize_all(topic, sources, llm)
            evidence = "\n\n".join(s.digest for s in sources if s.digest)

        plan = await self._plan_nodes(topic, evidence, llm)
        nodes = plan.nodes[: self.config.max_nodes]
        node_titles = [n.title for n in nodes]

        node_bodies = await asyncio.gather(
            *[
                self._node_body(i + 1, n, node_titles, evidence, llm)
                for i, n in enumerate(nodes)
            ]
        )

        node_paths: list[Path] = []
        for i, (node, body) in enumerate(zip(nodes, node_bodies), 1):
            path = tree_dir / f"N{i}-{_filename_safe(node.title)}.md"
            write_text(path, body)
            node_paths.append(path)

        cross_refs = {n.title: [t for t in node_titles if t != n.title] for n in nodes}
        main_table = await self._main_table(plan, node_paths, sources, llm)
        main_path = tree_dir / "00-主表.md"
        write_text(main_path, main_table)

        return OrganizeResult(
            main_table=main_path, nodes=node_paths, cross_refs=cross_refs, tree_dir=tree_dir
        )

    # -- map：逐篇摘要 -------------------------------------------------- #

    async def _summarize_all(
        self, topic: str, sources: list[_SourceDoc], llm: LLMClient
    ) -> None:
        await asyncio.gather(*[self._summarize_doc(topic, s, llm) for s in sources])

    async def _summarize_doc(self, topic: str, s: _SourceDoc, llm: LLMClient) -> None:
        chunks = _chunk(s.text, _DOC_CHUNK)[:_MAX_CHUNKS_PER_DOC]
        subject = topic or "该资料的主题"

        async def _one(chunk: str) -> list[str]:
            prompt = (
                f"下面是来自《{s.title or s.url}》(标记为 来源{s.sid}) 的资料片段。\n"
                f"围绕调研主题「{subject}」，提取该片段中与主题相关的关键信息点："
                "论文/资料的研究问题、方法或模型名、关键发现与数据、结论与应用场景。\n"
                "每条尽量具体、可直接引用，保留专有名词/模型名/数据。"
                "与主题无关的内容忽略。返回 JSON：{points:[...]}。\n\n"
                f"--- 片段 ---\n{chunk}"
            )
            try:
                res = await llm.chat_structured(prompt, _Points, system=_SUM_SYSTEM)
                return res.points
            except Exception:  # noqa: BLE001
                return []

        results = await asyncio.gather(*[_one(c) for c in chunks])
        points: list[str] = []
        for r in results:
            points.extend(r)
        s.points = points[:18]  # 单篇要点上限，控制后续 prompt 体积
        head = f"【来源{s.sid}｜{s.title or s.url}】"
        s.digest = head + "\n" + "\n".join(f"- {p}" for p in s.points) if s.points else ""

    def _structured_evidence(self, input_dir: Path) -> str:
        entities = read_json(input_dir / "entities.json")
        rel = input_dir / "relations.json"
        tri = input_dir / "triples.json"
        blob = {
            "entities": entities[:200],
            "relations": read_json(rel)[:200] if rel.exists() else [],
            "triples": read_json(tri)[:200] if tri.exists() else [],
        }
        return json.dumps(blob, ensure_ascii=False, indent=1)[:60000]

    # -- reduce：规划 + 写节点 ----------------------------------------- #

    async def _plan_nodes(self, topic: str, evidence: str, llm: LLMClient) -> _NodePlan:
        subject = topic or "下述资料所讨论的主题"
        prompt = (
            f"调研主题是：「{subject}」。\n"
            "下面是各来源资料的要点摘要（每段开头标注了来源编号）。\n"
            f"请据此把主题拆分为 {self.config.min_nodes}-{self.config.max_nodes} 个子主题节点，"
            "组织成知识树。要求：\n"
            f"- 每个节点是「{subject}」的一个具体方面/子问题，不要把'知识树/调研方法'当主题；\n"
            "- 节点应尽量覆盖各来源（尤其论文）的核心内容；\n"
            "- 每个节点回答一个核心问题，节点间有因果或递进关系。\n"
            "返回 JSON：{nodes:[{title, core_question}], main_thread}。\n\n"
            f"--- 各来源要点 ---\n{evidence}"
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
            f"为知识树节点撰写**详实**内容。节点：{node.title}\n"
            f"核心问题：{node.core_question}\n其他节点：{others}\n\n"
            "下面是各来源资料要点（开头标注来源编号）。请据此写作，"
            "**每条论据必须标注来源编号（如 来源03）**，让读者能追溯到具体资料；"
            "涉及论文时点明其方法/模型/发现/数据。\n\n"
            "严格按以下 Markdown 模板输出（保留 S1-S4 小节标题）：\n"
            f"# N{idx} {node.title}\n\n> 核心问题：{node.core_question}\n\n"
            "## S1 原始资料依据\n（按来源分组，逐条摘录关键依据并标注 来源NN）\n\n"
            "## S2 核心观点提炼\n（4-6 条，每条标注支撑来源）\n\n"
            "## S3 与其他节点的关系\n\n"
            "## S4 在主线中的位置\n\n"
            f"--- 各来源要点 ---\n{evidence}"
        )
        return await llm.chat(prompt, system=_SYSTEM)

    async def _main_table(
        self,
        plan: _NodePlan,
        node_paths: list[Path],
        sources: list[_SourceDoc],
        llm: LLMClient,
    ) -> str:
        index = "\n".join(
            f"- [N{i} {p.stem.split('-', 1)[-1]}]({p.name})"
            for i, p in enumerate(node_paths, 1)
        )
        node_list = "\n".join(f"- {n.title}：{n.core_question}" for n in plan.nodes)
        prompt = (
            "为知识树撰写主表。包含：1) 节点索引（每节点一句话核心问题）；"
            "2) 主线流程（因果/递进）；3) 跨节点交叉关联表。直接输出 Markdown。\n\n"
            f"节点清单：\n{node_list}\n\n主线提示：{plan.main_thread}"
        )
        body = await llm.chat(prompt, system=_SYSTEM)
        refs = ""
        if sources:
            refs = "\n\n## 来源索引\n" + "\n".join(
                f"- 来源{s.sid}：{s.title or '(无标题)'} — {s.url}" for s in sources
            )
        return f"# 知识体系总览\n\n## 节点索引\n{index}\n\n{body}\n{refs}\n"


async def organize(
    extracted_dir: Path, config: OrganizerConfig, llm: LLMClient
) -> OrganizeResult:
    """模块级函数（01 §5.2 签名）。"""
    return await Organizer(config).run(extracted_dir, llm)
