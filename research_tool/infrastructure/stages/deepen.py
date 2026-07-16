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

import logging

from ..llm.base import LLMClient
from ...domain.errors import LLMAuthenticationError
from ...domain.models import DeepenConfig, DeepenResult
from .base import write_text
from .collector import Collector

logger = logging.getLogger(__name__)

# 抓取时写入的注释头：<!-- title: ... -->
_TITLE_RE = re.compile(r"<!--\s*title:\s*(.*?)\s*-->")
_COMMENT_LINE = re.compile(r"^\s*<!--.*?-->\s*$")

_DONE_MARKER = ".deepen_done"


class _Entities(BaseModel):
    entities: list[str] = Field(default_factory=list)


class _Queries(BaseModel):
    queries: list[str] = Field(default_factory=list)


class _TimelineNode(BaseModel):
    """画像中的一段经历（P2-4）。period_from/to 用于回溯搜索带时间窗口。"""

    period_from: int | None = None  # 起始年（含），None=未知
    period_to: int | None = None  # 截止年（含），None=至今/未知
    institution: str = ""
    role: str = ""  # 博士/硕士/讲师/...
    confidence: float = 0.0  # 该节点的置信度


class _Profile(BaseModel):
    """从 raw/ 摘要抽出的结构化画像。P1 用于生成去锚注入查询；
    P2-4 扩展 timeline + confidence 支持时间线回溯与迭代终止判定。"""

    name: str = ""  # 主名（中文/原文）
    name_en: str | None = None  # 英文名（人物常被英文论文收录）
    aliases: list[str] = Field(default_factory=list)  # 别名/曾用名
    institutions: list[str] = Field(default_factory=list)  # 关联机构
    fields: list[str] = Field(default_factory=list)  # 研究领域/方向
    keywords: list[str] = Field(default_factory=list)  # 代表关键词/方法名
    timeline: list[_TimelineNode] = Field(default_factory=list)  # P2-4 时间线
    confidence: float = 0.0  # P2-4 整体置信度 0-1


class _OwnsBatch(BaseModel):
    """同名消歧批量返回：与输入文件顺序一一对应。"""

    owns: list[bool] = Field(default_factory=list)


_SYSTEM = "你是严谨的调研规划专家，擅长拆解话题、发现信息缺口、设计互补的搜索查询。"


class DeepenStage:
    """反偏差深挖阶段。复用同一个 Collector 实例做补充搜索+抓取。"""

    def __init__(self, config: DeepenConfig, collector: Collector, llm: LLMClient) -> None:
        self.config = config
        self.collector = collector
        self.llm = llm

    async def run(  # noqa: PLR0915  # deepen.run; split pending (P1 seed item 6)
        self, topic: str, raw_dir: Path, *, core_keyword: str | None = None
    ) -> DeepenResult:
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

        # --- 画像增强（P1）：从已采 raw/ 抽结构化画像 → 注入去锚查询 --- #
        # 单独一轮（不与实体查询挤占 breadth）；失败/关闭则跳过，不阻断管道。
        profile: _Profile | None = None
        if self.config.profile_extract:
            profile = await self._extract_profile(topic, raw_dir, core_keyword)
            if profile is not None:
                await _run_queries(self._profile_queries(profile))

        # --- 画像迭代（P2-4）：timeline 回溯 + 同名消歧 + 重抽画像 ----- #
        # profile_iterations >=2 启用；每轮检查新增文件数与画像置信度终止条件。
        for _ in range(max(0, self.config.profile_iterations - 1)):
            if profile is None:
                break
            files_before = len(new_files)
            if self.config.timeline_backtrack and profile.timeline:
                t_files, t_warn = await self._timeline_search(profile, topic, raw_dir)
                new_files.extend(t_files)
                warnings.extend(t_warn)
            if self.config.disambiguation:
                moved = await self._disambiguate(raw_dir, profile, topic)
                if moved:
                    warnings.append(f"消歧移走 {moved} 份疑似同名/无关资料到 raw/_disambig/")
            # 重抽画像（基于新加 / 消歧后的 raw）
            refreshed = await self._extract_profile(topic, raw_dir, core_keyword)
            if refreshed is not None:
                profile = refreshed
            # 终止判定
            added = len(new_files) - files_before
            if added < self.config.min_new_files_per_iter:
                break
            if profile.confidence >= self.config.min_profile_confidence:
                break

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
        except LLMAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 - 非鉴权失败回退，不阻断管道
            logger.debug("实体拆分失败: %s", exc)
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
        except LLMAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("实体查询生成失败: %s", exc)
            # 回退：实体本身 + 英文/学术角度的朴素模板
            fallback = list(entities)
            for e in entities:
                fallback.append(f"{e} 论文")
            return fallback
        qs = [q.strip() for q in res.queries if q.strip()]
        return qs or list(entities)

    async def _extract_profile(
        self, topic: str, raw_dir: Path, core_keyword: str | None
    ) -> _Profile | None:
        """读 raw/ 摘要，让 LLM 抽结构化画像（中英文名/机构/领域/关键词）。

        core_keyword 给定时聚焦该核心实体（如 topic 含机构名、要查的是其中的人物）。
        无内容/抽取失败/画像为空时返回 None（调用方跳过，不阻断管道）。
        """
        digest = self._read_digest(raw_dir)
        if not digest:
            return None
        focus = core_keyword or topic
        prompt = (
            f"调研话题：「{topic}」，核心实体：「{focus}」。\n"
            f"下面是已采集资料的标题与摘要片段：\n\n{digest}\n\n"
            f"请围绕核心实体「{focus}」抽取一份结构化画像，用于设计更精准的补充检索。\n"
            "字段：\n"
            "- name：主名（中文或原文）；\n"
            "- name_en：英文名/拉丁化姓名（人物论文常以英文收录；无则 null）；\n"
            "- aliases：别名/曾用名（无则空）；\n"
            "- institutions：关联机构（学校/实验室/公司，按出现顺序）；\n"
            "- fields：研究领域/方向；\n"
            "- keywords：代表性方法名/模型名/关键词；\n"
            "- timeline：经历时间线，每段 {period_from, period_to, institution, role, "
            "confidence}（如 {period_from:2020, period_to:2024, institution:'南洋理工', "
            "role:'博士', confidence:0.7}）。年份未知留 null；只填资料中可考证的段。\n"
            "- confidence：你对整张画像的整体置信度 0-1（信息越多越具体则越高）。\n"
            "只填**资料中有依据**的内容，不要臆造；不确定的留空。\n"
            "返回 JSON：{name, name_en, aliases:[...], institutions:[...], "
            "fields:[...], keywords:[...], timeline:[...], confidence}。"
        )
        try:
            profile = await self.llm.chat_structured(prompt, _Profile, system=_SYSTEM)
        except LLMAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 - 非鉴权失败跳过
            logger.debug("画像抽取失败: %s", exc)
            return None
        # 全空画像无价值
        if not any(
            (
                profile.name_en,
                profile.aliases,
                profile.institutions,
                profile.fields,
                profile.keywords,
            )
        ):
            return None
        return profile

    async def _timeline_search(
        self,
        profile: _Profile,
        topic: str,
        raw_dir: Path,
    ) -> tuple[list[Path], list[str]]:
        """P2-4：对画像每段经历做带时间窗口的回溯搜索。

        每段生成 "<anchor> <institution>" 查询，传 from_year/to_year 给
        search_queries 的显式单次通道（绕开 deep_search 矩阵展开）。返回新增
        文件与 warnings。
        """
        anchor = profile.name_en or profile.name
        if not anchor or not profile.timeline:
            return [], []
        new_files: list[Path] = []
        warnings: list[str] = []
        for node in profile.timeline[: self.config.breadth]:
            inst = node.institution.strip()
            if not inst:
                continue
            q = f"{anchor} {inst}"
            sr = await self.collector.search_queries(
                [q],
                from_year=node.period_from,
                to_year=node.period_to,
                sort=None,
                offset=0,  # 显式触发单次模式
            )
            warnings.extend(sr.warnings)
            res = await self.collector.fetch_and_store(topic, sr.hits, raw_dir)
            warnings.extend(res.warnings)
            new_files.extend(res.files)
        return new_files, warnings

    async def _disambiguate(
        self,
        raw_dir: Path,
        profile: _Profile,
        topic: str,
    ) -> int:
        """P2-4：同名消歧。LLM 按画像（机构+领域）判每份 raw 文件归属，
        他人的移到 raw/_disambig/。返回移走数量。

        安全机制：画像 confidence < 0.7 时跳过，避免初次画像不准时误杀。
        """
        if profile.confidence < 0.7:
            return 0
        if not profile.institutions and not profile.fields:
            return 0
        titles = [
            (p, _TITLE_RE.search(p.read_text("utf-8", errors="ignore")))
            for p in sorted(raw_dir.glob("*.md"))
        ]
        items = [(p, m.group(1) if m else p.stem) for p, m in titles]
        if not items:
            return 0

        anchor = profile.name_en or profile.name
        inst_hint = "/".join(profile.institutions[:3])
        field_hint = "/".join(profile.fields[:3])
        moved = 0
        bucket = raw_dir / "_disambig"

        batch_size = max(self.config.breadth, 10)
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            prompt = (
                f"调研主题：「{topic}」，核心实体：「{anchor}」"
                f"（关联机构：{inst_hint}；研究领域：{field_hint}）。\n"
                f"下面是 {len(batch)} 份资料的标题，请判断**是否属于该核心实体**"
                "（同名不同人/无关页面/广告页等返回 false）：\n\n"
                + "\n".join(f"[{i}] {t}" for i, (_, t) in enumerate(batch))
                + f"\n\n返回 JSON：{{owns:[{len(batch)} 个布尔，顺序对应]}}。"
            )
            try:
                res = await self.llm.chat_structured(prompt, _OwnsBatch, system=_SYSTEM)
                owns = list(res.owns)
            except LLMAuthenticationError:
                raise
            except Exception as exc:  # noqa: BLE001 - 非鉴权失败则该批默认全部保留
                logger.debug("同名消歧失败: %s", exc)
                continue
            for i, (path, _) in enumerate(batch):
                if i < len(owns) and owns[i] is False and path.exists():
                    bucket.mkdir(exist_ok=True)
                    target = bucket / path.name
                    if target.exists():
                        target.unlink()
                    path.rename(target)
                    moved += 1
        return moved

    def _profile_queries(self, profile: _Profile) -> list[str]:
        """据画像生成去锚注入查询：英文名 × 机构/领域、别名、关键词组合。"""
        names = [n for n in [profile.name_en, profile.name] if n]
        anchor = profile.name_en or profile.name  # 英文名优先（突破中文锚定）
        queries: list[str] = list(names)
        if anchor:
            for inst in profile.institutions[:3]:
                queries.append(f"{anchor} {inst}")
            for field in profile.fields[:3]:
                queries.append(f"{anchor} {field}")
        queries += list(profile.aliases)
        for kw in profile.keywords[:3]:
            if anchor:
                queries.append(f"{anchor} {kw}")
        # 去空去重
        return list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))

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
        except LLMAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("查询生成失败: %s", exc)
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
            body = "\n".join(ln for ln in text.splitlines() if not _COMMENT_LINE.match(ln)).strip()
            excerpt = body[: self.config.per_file_chars]
            block = f"- 《{title}》：{excerpt}"
            if total + len(block) > self.config.max_input_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)


async def deepen(
    topic: str,
    config: DeepenConfig,
    collector: Collector,
    llm: LLMClient,
    raw_dir: Path,
    *,
    core_keyword: str | None = None,
) -> DeepenResult:
    """模块级便捷函数。"""
    return await DeepenStage(config, collector, llm).run(topic, raw_dir, core_keyword=core_keyword)
