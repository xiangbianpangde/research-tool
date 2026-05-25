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
from ..search.base import SearchHit, SearchResult
from .base import (
    domain_of,
    ensure_dir,
    read_json,
    safe_filename,
    write_json,
    write_text,
)
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
        f"我要检索关于「{topic}」的**学术论文**。请设计 {n} 个高质量搜索查询。\n"
        "要求：\n"
        "- 多数用**英文**（该领域论文以英文为主），可保留 1-2 个中文查询；\n"
        "- 准确判断该主题对应的**研究子领域及其标准英文术语**，不要望文生义"
        "（例如中文'图谱'在不同语境可能指 knowledge graph，也可能指 graph/GNN，"
        "请结合'上下文学习'判断真正的子领域）；\n"
        "- 尽量列出该子领域**代表性方法名/模型名/论文名**作为查询关键词；\n"
        "- 可加 'arxiv' 或 'paper' 提高论文命中率；\n"
        "- 由宽泛到精确，覆盖核心概念、代表方法、应用与对比。\n"
        f"（语言倾向：{lang_hint}）\n"
        "返回 JSON：{queries:[...]}（纯查询短语，不要编号）。"
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


def _url_hash(url: str) -> str:
    """URL 短哈希，用于文件名去重后缀（防覆盖：修复 3 / 风险 9）。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:6]


class Collector:
    def __init__(
        self, config: CollectorConfig | None = None, llm: LLMClient | None = None
    ) -> None:
        self.config = config or CollectorConfig()
        self.llm = llm

    async def search_only(self, topic: str) -> SearchResult:
        """多轮 × 多引擎搜索，按 URL 去重（方法论 1.1）。供 --dry-run 与 depth=1 用。

        返回 SearchResult（hits + warnings）：搜索后端失败不再静默丢弃（修复 1）。
        """
        if self.config.llm_query_expansion and self.llm is not None:
            queries = await _llm_build_queries(
                topic, self.config.search_rounds, self.config.language, self.llm
            )
        else:
            queries = _build_queries(
                topic, self.config.search_rounds, self.config.language
            )
        # 用户显式查询置顶（点名要找的论文/方法），保序去重
        if self.config.extra_queries:
            queries = list(dict.fromkeys(self.config.extra_queries + queries))
        return await self.search_queries(queries)

    async def search_queries(self, queries: list[str]) -> SearchResult:
        """对给定查询列表跑全部引擎，受 max_concurrent_searches 限流（修复 2），
        失败收集为 warnings（修复 1），按 URL 去重。Deepen 阶段也复用本方法。"""
        sem = asyncio.Semaphore(self.config.max_concurrent_searches)

        async def _one(backend, query: str) -> list[SearchHit]:
            async with sem:
                return await backend.search(
                    query, self.config.max_results_per_engine, self.config.language
                )

        tasks = []
        meta: list[tuple[str, str]] = []  # (engine, query)，用于失败时定位
        for engine in self.config.search_engines:
            backend = get_backend(engine, self.config)
            for query in queries:
                meta.append((engine, query))
                tasks.append(_one(backend, query))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        hits: list[SearchHit] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for (engine, query), res in zip(meta, results):
            if isinstance(res, Exception):
                warnings.append(f"{engine} 搜索失败 query={query!r}: {res}")
                continue
            for hit in res:
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                hits.append(hit)
                if len(hits) >= self.config.max_total_results:
                    return SearchResult(hits=hits, warnings=warnings)
        return SearchResult(hits=hits, warnings=warnings)

    async def run(
        self, topic: str, work_dir: Path, *, dry_run: bool = False
    ) -> CollectResult:
        raw_dir = ensure_dir(Path(work_dir) / "raw")
        sr = await self.search_only(topic)

        if dry_run:
            return CollectResult(
                files=[], sources=[], raw_dir=raw_dir, warnings=sr.warnings
            )

        result = await self.fetch_and_store(topic, sr.hits, raw_dir)
        result.warnings = sr.warnings + result.warnings
        return result

    async def fetch_and_store(
        self, topic: str, hits: list[SearchHit], raw_dir: Path
    ) -> CollectResult:
        """抓取 hits 并写入 raw/，幂等且防覆盖。

        - 按 sources.json 已记录的 URL 去重（同一 URL 不重复抓取/写入）
        - 文件名 idx 从现有文件数续编、并加 URL 短哈希后缀，避免多次调用
          （含 Deepen 复用本方法）相互覆盖（修复 3 / 风险 9）
        - 写完后合并 sources.json（风险 8：来源不断裂）
        """
        ensure_dir(raw_dir)
        existing_urls = {s.get("url") for s in self._load_sources(raw_dir)}
        todo = [h for h in hits if h.url not in existing_urls]

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

        fetched = await asyncio.gather(*[_fetch(h) for h in todo]) if todo else []

        # depth==3：对成功页追踪少量二级内部链接
        if self.config.depth >= 3 and fetched:
            extra = await self._follow_links(list(fetched), fetcher, sem, topic)
            fetched = list(fetched) + extra

        files: list[Path] = []
        sources: list[Source] = []
        idx = self._next_idx(raw_dir)
        written_urls: set[str] = set(existing_urls)
        for hit, fr in fetched:
            if not fr.ok or not fr.markdown.strip():
                continue
            # 垃圾过滤：正文过短(登录页/导航页/JS空壳)直接丢弃
            if len(fr.markdown.strip()) < self.config.min_doc_chars:
                continue
            if hit.url in written_urls:  # 二级链接也可能撞已有 URL
                continue
            written_urls.add(hit.url)
            idx += 1
            domain = domain_of(hit.url)
            fname = f"{idx:02d}-{safe_filename(domain)}-{_url_hash(hit.url)}.md"
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

        self._append_sources(raw_dir, sources)
        return CollectResult(files=files, sources=sources, raw_dir=raw_dir)

    # -- sources.json / 文件名 辅助 ------------------------------------- #

    @staticmethod
    def _load_sources(raw_dir: Path) -> list[dict]:
        path = raw_dir / "sources.json"
        if not path.exists():
            return []
        try:
            data = read_json(path)
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _append_sources(self, raw_dir: Path, new_sources: list[Source]) -> None:
        """把新来源合并进 sources.json（按 URL 去重），保证溯源完整（风险 8）。"""
        existing = self._load_sources(raw_dir)
        by_url = {s.get("url"): s for s in existing}
        for s in new_sources:
            by_url[s.url] = s.model_dump()
        write_json(raw_dir / "sources.json", list(by_url.values()))

    @staticmethod
    def _next_idx(raw_dir: Path) -> int:
        """续编文件序号：现有 *.md 数量，避免重置为 0 覆盖既有文件（修复 3）。"""
        return len([p for p in raw_dir.glob("*.md")])

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
