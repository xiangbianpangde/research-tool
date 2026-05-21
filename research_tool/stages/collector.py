"""Stage 1: Collector —— 搜索 + 抓取。

依据 01 §2、02 §2、05 §3 Stage1。
流程：多引擎搜索 → URL 去重 → 并行抓取（Crawl4AI）→ 写 raw/XX-{domain}.md
+ sources.json。depth 控制抓取深度（1=仅搜索 2=抓结果页 3=追二级链接）。
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..models import CollectorConfig, CollectResult, Source
from ..search import get_backend
from ..search.base import SearchHit
from .base import domain_of, ensure_dir, safe_filename, write_json, write_text
from .fetcher import FetchResult, Fetcher


# 多轮搜索关键词扩展模板（方法论 1.1：由宽泛到精确、中英文并行、学术+通俗）
_ROUND2 = {  # 交叉领域 / 相关概念
    "zh": ["综述", "原理", "相关概念"],
    "en": ["overview", "survey", "fundamentals"],
}
_ROUND3 = {  # 补充和细化
    "zh": ["应用", "对比", "局限"],
    "en": ["applications", "comparison", "limitations"],
}


def _round_langs(language: str) -> list[str]:
    if language == "both":
        return ["zh", "en"]
    return [language]


def _build_queries(topic: str, rounds: int, language: str) -> list[str]:
    """按轮次构造去重后的查询列表（方法论 1.1，模板扩展）。"""
    queries: list[str] = [topic]  # 第1轮：核心关键词
    langs = _round_langs(language)
    if rounds >= 2:
        for lang in langs:
            queries += [f"{topic} {m}" for m in _ROUND2[lang]]
    if rounds >= 3:
        for lang in langs:
            queries += [f"{topic} {m}" for m in _ROUND3[lang]]
    # 保序去重
    return list(dict.fromkeys(queries))


class _Queries(BaseModel):
    queries: list[str] = Field(default_factory=list)


_QUERY_SYSTEM = "你是检索专家，擅长为调研主题设计互补、覆盖面广的搜索查询。"


async def _llm_build_queries(
    topic: str, rounds: int, language: str, llm: LLMClient
) -> list[str]:
    """用 LLM 生成贴主题的多轮查询：核心→交叉/相关→应用/对比。"""
    n = rounds * 3
    lang_hint = {
        "zh": "全部用中文",
        "en": "全部用英文",
        "both": "中英文混合",
    }.get(language, "中英文混合")
    prompt = (
        f"为调研主题「{topic}」设计 {n} 个搜索查询（{lang_hint}）。\n"
        "要求覆盖：核心概念、交叉/相关领域、应用与对比，由宽泛到精确。\n"
        "查询应是可直接投喂搜索引擎的短语，不要编号。\n"
        "返回 JSON：{queries:[...]}。"
    )
    try:
        res = await llm.chat_structured(prompt, _Queries, system=_QUERY_SYSTEM)
    except Exception:  # noqa: BLE001 - 失败则回退模板
        return _build_queries(topic, rounds, language)
    queries = [topic] + [q.strip() for q in res.queries if q.strip()]
    return list(dict.fromkeys(queries)) or [topic]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class Collector:
    def __init__(
        self, config: CollectorConfig | None = None, llm: LLMClient | None = None
    ) -> None:
        self.config = config or CollectorConfig()
        self.llm = llm

    async def search_only(self, topic: str) -> list[SearchHit]:
        """多轮 × 多引擎搜索，按 URL 去重（方法论 1.1）。供 --dry-run 与 depth=1 用。"""
        if self.config.llm_query_expansion and self.llm is not None:
            queries = await _llm_build_queries(
                topic, self.config.search_rounds, self.config.language, self.llm
            )
        else:
            queries = _build_queries(
                topic, self.config.search_rounds, self.config.language
            )
        tasks = []
        for engine in self.config.search_engines:
            backend = get_backend(engine, self.config)
            for query in queries:
                tasks.append(
                    backend.search(
                        query,
                        self.config.max_results_per_engine,
                        self.config.language,
                    )
                )
        results = await asyncio.gather(*tasks, return_exceptions=True)

        hits: list[SearchHit] = []
        seen: set[str] = set()
        for res in results:
            if isinstance(res, Exception):
                continue
            for hit in res:
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
                if len(hits) >= self.config.max_total_results:
                    return hits
        return hits

    async def run(
        self, topic: str, work_dir: Path, *, dry_run: bool = False
    ) -> CollectResult:
        raw_dir = ensure_dir(Path(work_dir) / "raw")
        hits = await self.search_only(topic)

        if dry_run:
            return CollectResult(files=[], sources=[], raw_dir=raw_dir)

        fetcher = Fetcher(
            timeout_sec=self.config.timeout_sec,
            parse_pdf=self.config.parse_pdf,
            mineru_cmd=self.config.mineru_cmd,
            pdf_dir=raw_dir / "_pdfs",
        )
        sem = asyncio.Semaphore(self.config.concurrency)

        async def _fetch(hit: SearchHit) -> tuple[SearchHit, FetchResult]:
            async with sem:
                if self.config.depth >= 2:
                    fr = await fetcher.fetch(hit.url)
                else:
                    # depth==1：不抓取，用搜索摘要作为内容
                    fr = FetchResult(hit.url, hit.snippet, ok=bool(hit.snippet))
                return hit, fr

        fetched = await asyncio.gather(*[_fetch(h) for h in hits])

        # depth==3：对成功页追踪少量二级内部链接
        if self.config.depth >= 3:
            extra = await self._follow_links(fetched, fetcher, sem, topic)
            fetched.extend(extra)

        files: list[Path] = []
        sources: list[Source] = []
        idx = 0
        for hit, fr in fetched:
            if not fr.ok or not fr.markdown.strip():
                continue
            # 垃圾过滤：正文过短(登录页/导航页/JS空壳)直接丢弃
            if len(fr.markdown.strip()) < self.config.min_doc_chars:
                continue
            idx += 1
            domain = domain_of(hit.url)
            fname = f"{idx:02d}-{safe_filename(domain)}.md"
            fpath = raw_dir / fname
            via = f'{hit.source_engine}_search("{topic}")'
            header = (
                f"<!-- source: {hit.url} -->\n"
                f"<!-- title: {hit.title} -->\n"
                f"<!-- fetched: {_now_iso()} -->\n"
                f"<!-- via: {via} -->\n\n"
            )
            write_text(fpath, header + fr.markdown)
            files.append(fpath)
            sources.append(
                Source(
                    url=hit.url,
                    title=hit.title,
                    fetched_at=_now_iso(),
                    source_engine=hit.source_engine,
                    content_hash=_sha256(fr.markdown),
                )
            )

        write_json(raw_dir / "sources.json", [s.model_dump() for s in sources])
        return CollectResult(files=files, sources=sources, raw_dir=raw_dir)

    async def _follow_links(
        self,
        fetched: list[tuple[SearchHit, FetchResult]],
        fetcher: Fetcher,
        sem: asyncio.Semaphore,
        topic: str,
        per_page: int = 2,
    ) -> list[tuple[SearchHit, FetchResult]]:
        seen = {hit.url for hit, _ in fetched}
        targets: list[str] = []
        for _, fr in fetched:
            for link in fr.links[:per_page]:
                if link not in seen:
                    seen.add(link)
                    targets.append(link)

        async def _f(url: str):
            async with sem:
                fr = await fetcher.fetch(url)
                hit = SearchHit(url=url, title="", source_engine="secondary")
                return hit, fr

        if not targets:
            return []
        return list(await asyncio.gather(*[_f(u) for u in targets]))


async def collect(
    topic: str, config: CollectorConfig, work_dir: Path
) -> CollectResult:
    """模块级函数（01 §2.2 签名）。"""
    return await Collector(config).run(topic, work_dir)
