"""Stage 1.5: Deepen —— 反偏差递归深挖。

依据升级计划 §4。在 Collect 与 Clean 之间插入一个可选阶段，针对 raw/ 已采
内容做三件事，并把新结果补写回同一个 raw/：

  机制 A（解决偏差）：实体拆分 → 每个实体多视角独立搜索
      例 "康怡琳 中南民族大学" → ["康怡琳", "中南民族大学"]
      → "Yilin Kang" / "康怡琳 博士" / "康怡琳 论文" …（不被单一机构锚点绑架）
  机制 B（解决浅度）：扫描已采内容识别缺失维度 → 生成补充查询，逐轮深挖
  机制 C（解决矛盾）：检测冲突信息 → 反向搜索验证

设计约束（问题清单）：
  - 风险 4：喂 LLM 做缺口分析的内容必须截断（每文件标题+前 N 字，总量封顶）
  - 风险 5：写独立完成标记 raw/.deepen_done，避免 resume 误跳
  - 风险 7：实体数 ≤ max_entities，单实体跳过拆分
  - 风险 8/9：补采复用 Collector.fetch_and_store（幂等去重 + 合并 sources.json）
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..models import DeepenConfig, DeepenResult
from .base import write_text
from .collector import Collector

# 抓取时写入的注释头：<!-- title: ... -->
_TITLE_RE = re.compile(r"<!--\s*title:\s*(.*?)\s*-->")
_COMMENT_LINE = re.compile(r"^\s*<!--.*?-->\s*$")

_DONE_MARKER = ".deepen_done"


class _Entities(BaseModel):
    entities: list[str] = Field(default_factory=list)


class _Queries(BaseModel):
    queries: list[str] = Field(default_factory=list)


_SYSTEM = "你是严谨的调研规划专家，擅长拆解话题、发现信息缺口、设计互补的搜索查询。"


class DeepenStage:
    """反偏差深挖阶段。复用同一个 Collector 实例做补充搜索+抓取。"""

    def __init__(
        self, config: DeepenConfig, collector: Collector, llm: LLMClient
    ) -> None:
        self.config = config
        self.collector = collector
        self.llm = llm

    async def run(self, topic: str, raw_dir: Path) -> DeepenResult:
        raw_dir = Path(raw_dir)
        warnings: list[str] = []
        new_files: list[Path] = []
        executed: list[str] = []
        done: set[str] = set()

        # --- 机制 A：实体拆分 + 多视角查询（第 1 轮）------------------- #
        entities = await self._split_entities(topic)
        round_queries = await self._entity_queries(topic, entities)

        async def _run_queries(cands: list[str]) -> None:
            cands = [q for q in dict.fromkeys(cands) if q and q not in done]
            cands = cands[: self.config.breadth]
            if not cands:
                return
            done.update(cands)
            executed.extend(cands)
            sr = await self.collector.search_queries(cands)
            warnings.extend(sr.warnings)
            res = await self.collector.fetch_and_store(topic, sr.hits, raw_dir)
            warnings.extend(res.warnings)
            new_files.extend(res.files)

        await _run_queries(round_queries)

        # --- 机制 B：缺口检测，逐轮深挖（第 2..depth 轮）-------------- #
        if self.config.gap_detection:
            for _ in range(max(0, self.config.depth - 1)):
                gaps = await self._detect_gaps(topic, raw_dir)
                if not gaps:
                    break
                await _run_queries(gaps)

        # --- 机制 C：矛盾检测 + 反向验证（收尾一轮）------------------- #
        if self.config.contradiction_check:
            contra = await self._detect_contradictions(topic, raw_dir)
            await _run_queries(contra)

        # 风险 5：独立完成标记，避免 resume 把 deepen 误判为已完成而跳过
        write_text(raw_dir / _DONE_MARKER, "deepen completed\n")
        return DeepenResult(
            entities=entities if len(entities) > 1 else [],
            queries=executed,
            new_files=new_files,
            warnings=warnings,
        )

    # -- LLM 步骤 -------------------------------------------------------- #

    async def _split_entities(self, topic: str) -> list[str]:
        """把话题拆成独立可检索的实体；单实体或失败时回退 [topic]（风险 7）。"""
        if not self.config.entity_split:
            return [topic]
        prompt = (
            f"把调研话题拆分为彼此独立、可单独作为搜索关键词的实体"
            f"（人物、机构、概念、产品等）。话题：「{topic}」\n"
            "要求：\n"
            f"- 最多 {self.config.max_entities} 个；\n"
            "- 只拆出能独立成立的实体，不要臆造、不要过度细分"
            "（如把一个技术名拆成多个子词）；\n"
            "- 若话题本身就是单一实体，返回它自己即可。\n"
            "返回 JSON：{entities:[...]}。"
        )
        try:
            res = await self.llm.chat_structured(prompt, _Entities, system=_SYSTEM)
        except Exception:  # noqa: BLE001 - 失败回退，不阻断管道
            return [topic]
        ents = [e.strip() for e in res.entities if e.strip()]
        ents = list(dict.fromkeys(ents))[: self.config.max_entities]
        return ents or [topic]

    async def _entity_queries(self, topic: str, entities: list[str]) -> list[str]:
        """对每个实体生成多视角查询（基础/学术/教育背景/英文名等）。"""
        multi = len(entities) > 1
        ent_hint = "、".join(entities)
        prompt = (
            f"为调研「{topic}」设计补充搜索查询，目标是消除"
            "「被单一锚点绑架」的偏差、覆盖各实体的独立信息。\n"
            f"已识别实体：{ent_hint}\n"
            "要求：\n"
            "- 对每个实体设计**独立**查询（不要总把它和其它实体捆在一起搜）；\n"
            "- 人物实体补充：英文名、'博士/PhD'、'论文/publications'、获奖等角度；\n"
            "- 机构/概念实体补充：定义、代表成果、关联方向；\n"
            f"- 总数不超过 {self.config.breadth} 个，优先最可能补齐空白的查询；\n"
            + ("- 多实体时优先保证每个实体至少 1 个独立查询。\n" if multi else "")
            + "返回 JSON：{queries:[...]}（纯查询短语，不要编号）。"
        )
        try:
            res = await self.llm.chat_structured(prompt, _Queries, system=_SYSTEM)
        except Exception:  # noqa: BLE001
            # 回退：实体本身 + 英文/学术角度的朴素模板
            fallback = list(entities)
            for e in entities:
                fallback.append(f"{e} 论文")
            return fallback
        qs = [q.strip() for q in res.queries if q.strip()]
        return qs or list(entities)

    async def _detect_gaps(self, topic: str, raw_dir: Path) -> list[str]:
        """读取已采内容摘要，让 LLM 识别缺失维度并生成补充查询。"""
        digest = self._read_digest(raw_dir)
        if not digest:
            return []
        prompt = (
            f"调研话题：「{topic}」。下面是已采集资料的标题与摘要片段：\n\n"
            f"{digest}\n\n"
            "请判断：相对于全面覆盖该话题，目前还**缺失哪些维度**"
            "（如背景、关键方法、对比、应用、局限、最新进展、关键人物/机构等）？\n"
            f"针对缺失维度生成至多 {self.config.breadth} 个补充搜索查询。\n"
            "返回 JSON：{queries:[...]}。"
        )
        return await self._safe_queries(prompt)

    async def _detect_contradictions(self, topic: str, raw_dir: Path) -> list[str]:
        """检测已采资料中的冲突说法，生成反向验证查询。"""
        digest = self._read_digest(raw_dir)
        if not digest:
            return []
        prompt = (
            f"调研话题：「{topic}」。已采集资料摘要：\n\n{digest}\n\n"
            "其中是否存在相互矛盾或存疑的说法（如时间/数据/归属冲突）？"
            f"若有，生成至多 {self.config.breadth} 个用于交叉验证的搜索查询；"
            "若无明显矛盾，返回空列表。\n"
            "返回 JSON：{queries:[...]}。"
        )
        return await self._safe_queries(prompt)

    async def _safe_queries(self, prompt: str) -> list[str]:
        try:
            res = await self.llm.chat_structured(prompt, _Queries, system=_SYSTEM)
        except Exception:  # noqa: BLE001
            return []
        return [q.strip() for q in res.queries if q.strip()]

    # -- raw/ 摘要（风险 4：上下文截断）--------------------------------- #

    def _read_digest(self, raw_dir: Path) -> str:
        """把 raw/*.md 压成「标题 + 前 N 字」摘要，总量封顶 max_input_chars。"""
        parts: list[str] = []
        total = 0
        for path in sorted(raw_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            title_m = _TITLE_RE.search(text)
            title = title_m.group(1) if title_m else path.stem
            body = "\n".join(
                ln for ln in text.splitlines() if not _COMMENT_LINE.match(ln)
            ).strip()
            excerpt = body[: self.config.per_file_chars]
            block = f"- 《{title}》：{excerpt}"
            if total + len(block) > self.config.max_input_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)


async def deepen(
    topic: str, config: DeepenConfig, collector: Collector, llm: LLMClient,
    raw_dir: Path,
) -> DeepenResult:
    """模块级便捷函数。"""
    return await DeepenStage(config, collector, llm).run(topic, raw_dir)
