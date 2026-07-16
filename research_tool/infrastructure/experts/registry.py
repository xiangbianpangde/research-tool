"""专家库注册表（ExpertLib）。

策展的可信实体（org / 作者 / 实验室），让其产出绕过搜索的默认排序（如 github
``sort=stars`` 降序 + top-N 截断）被保证纳入调研。两档注入：

- 强档：``seed_urls`` 直塞抓取队列（collector 把它们并入 ``extra_urls``），完全
  跳过搜索。适合已经点名、知道确切 URL 的高质量 repo/paper。
- 弱档：为有 github 句柄的专家生成 scoped 查询（``org:X <topic>``），命中结果由
  collector 豁免 top-N 截断。适合"某 org 的最新产出"这类不知道具体哪个的场景。

匹配用 topic 与 ``domains`` 的词重叠（v1 不上语义 embedding），库小时手工标签更可靠。
文件缺失或损坏时按空库回退、不崩管道，与 ``domain/config`` 的宽容加载一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ...common.logging_config import get_logger
from ...domain.models import ExpertEntry, ExpertLibrary

logger = get_logger(__name__)

# 归一化取词：小写 + 连字符转空格（"3d-vision" → {"3d", "vision"}），只保留 ascii 词。
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower().replace("-", " ")))


class ExpertRegistry:
    """从 experts.yaml 加载并提供匹配 / 注入能力。"""

    def __init__(self, library: ExpertLibrary) -> None:
        self.library = library

    # ------------------------------------------------------------------ #
    # 加载
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: str | Path) -> "ExpertRegistry":
        """读 experts.yaml，safe_load + schema 校验。缺失/损坏 → 空库回退（WARN）。"""
        p = Path(path)
        if not p.exists():
            logger.warning("专家库文件不存在，按空库运行: %s", p)
            return cls(ExpertLibrary())
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            library = ExpertLibrary.model_validate(raw)
        except Exception as e:  # noqa: BLE001  # 宽容：任何解析/校验错误都回退空库
            logger.warning("专家库解析失败，按空库运行: %s (%s)", p, e)
            return cls(ExpertLibrary())
        logger.info("专家库已加载：%d 个实体（%s）", len(library.experts), p)
        return cls(library)

    # ------------------------------------------------------------------ #
    # 匹配
    # ------------------------------------------------------------------ #
    def match(self, topic: str, min_overlap: float = 0.15) -> list[ExpertEntry]:
        """按 topic 与各专家 domains 的词重叠匹配。

        重叠率 = |topic 词 ∩ domains 词| / |topic 词|，即"topic 有多少比例落在该专家
        领域内"。≥ ``min_overlap`` 视为命中。结果按 (priority=high, 重叠率) 降序。
        """
        topic_tokens = _tokens(topic)
        if not topic_tokens:
            return []
        scored: list[tuple[bool, float, ExpertEntry]] = []
        for entry in self.library.experts:
            domain_tokens: set[str] = set()
            for d in entry.domains:
                domain_tokens |= _tokens(d)
            if not domain_tokens:
                continue
            overlap = len(topic_tokens & domain_tokens) / len(topic_tokens)
            if overlap >= min_overlap:
                scored.append((entry.priority == "high", overlap, entry))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [entry for _, _, entry in scored]

    # ------------------------------------------------------------------ #
    # 强档注入：seed_urls
    # ------------------------------------------------------------------ #
    def seed_urls_for(self, matched: list[ExpertEntry]) -> list[str]:
        """收集匹配专家的 seed_urls，去重保序。供 collector 并入 extra_urls。"""
        seen: set[str] = set()
        out: list[str] = []
        for entry in matched:
            for url in entry.seed_urls:
                if url and url not in seen:
                    seen.add(url)
                    out.append(url)
        return out

    # ------------------------------------------------------------------ #
    # 弱档注入：scoped 查询
    # ------------------------------------------------------------------ #
    def scoped_queries_for(
        self, matched: list[ExpertEntry], topic: str
    ) -> list[tuple[str, str]]:
        """为有 github 句柄的匹配专家生成 (engine, query)，如 ("github", "org:X topic")。

        返回可直接并入 collector 搜索查询集的 (引擎名, 查询串) 列表，去重。
        """
        core = topic.strip()
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for entry in matched:
            gh = (entry.handles or {}).get("github")
            if not gh:
                continue
            query = f"org:{gh} {core}".strip()
            key = ("github", query)
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out
