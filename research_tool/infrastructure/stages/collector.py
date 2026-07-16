"""Stage 1: Collector —— 搜索 + 抓取。

依据 01 §2、02 §2、05 §3 Stage1。
流程：多引擎搜索 → URL 去重 → 并行抓取（Crawl4AI）→ 写 raw/XX-{domain}.md
+ sources.json。depth 控制抓取深度（1=仅搜索 2=抓结果页 3=追二级链接）。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ...domain.models import CollectorConfig, CollectResult, Source, SourceAudit
from ..search import get_backend
from ..search._http import set_default_proxy
from ..search.base import SearchHit, SearchResult
from ..search.proxy_preflight import preflight_proxy
from .base import (
    domain_of,
    ensure_dir,
    read_json,
    safe_filename,
    write_json,
    write_text,
)
from .fetcher import FetchResult, Fetcher
from ...common.logging_config import get_logger, hash_url
from ...common.url_guard import assert_safe_url
from ...domain.errors import CollectError, LLMAuthenticationError, UrlBlockedError

logger = get_logger(__name__)

# 视频 host：命中则不走 Fetcher HTML，改写 discovery 笔记
# （完整转写仍靠 --video-url / process_videos）
_VIDEO_HOST_MARKERS = (
    "youtube.com/watch",
    "youtu.be/",
    "youtube.com/shorts/",
    "bilibili.com/video/",
    "b23.tv/",
)


def _is_video_url(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in _VIDEO_HOST_MARKERS)


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


def _facet_queries(core: str, facets: list[str]) -> list[str]:
    """Phase2（去锚）查询：core 单独成查询 + core×每个 facet 维度展开。

    P1 关键修正：facets 只在 Phase2 生效。core 单查（如 "康怡琳"）比含机构名的
    整条 topic 更窄更精准，能突破单一锚点；facet 组合（"康怡琳 南洋理工"）扩维度。
    """
    core = core.strip()
    if not core:
        return []
    queries = [core]
    queries += [f"{core} {f.strip()}" for f in facets if f.strip()]
    return list(dict.fromkeys(queries))


class _Queries(BaseModel):
    queries: list[str] = Field(default_factory=list)


_QUERY_SYSTEM = "你是检索专家，擅长为调研主题设计互补、覆盖面广的搜索查询。"


async def _llm_build_queries(topic: str, rounds: int, language: str, llm: LLMClient) -> list[str]:
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
    except LLMAuthenticationError:
        raise
    except Exception:  # noqa: BLE001 - 非鉴权失败则回退模板
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


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "using",
    "review",
    "survey",
    "study",
    "paper",
    "application",
    "applications",
    "model",
    "models",
    "large",
    "language",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _hit_relevance(topic: str, hit: SearchHit) -> float:
    """Cheap pre-fetch relevance score based on topic/query vs title/snippet overlap."""
    topic_tokens = _tokens(topic)
    if not topic_tokens:
        return 1.0
    hit_tokens = _tokens(f"{hit.title}\n{hit.snippet}\n{hit.url}")
    if not hit_tokens:
        return 0.0
    return len(topic_tokens & hit_tokens) / len(topic_tokens)


class Collector:
    def __init__(self, config: CollectorConfig | None = None, llm: LLMClient | None = None) -> None:
        self.config = config or CollectorConfig()
        self.llm = llm
        self._proxy_preflight_done = False

    @staticmethod
    def _increment(audit: SourceAudit, **counts: int) -> SourceAudit:
        """Return an updated immutable audit counter."""
        return audit.model_copy(
            update={name: getattr(audit, name) + value for name, value in counts.items()}
        )

    @staticmethod
    def _merge_audits(*groups: list[SourceAudit]) -> list[SourceAudit]:
        merged: dict[str, SourceAudit] = {}
        for group in groups:
            for incoming in group:
                current = merged.get(incoming.engine, SourceAudit(engine=incoming.engine))
                merged[incoming.engine] = SourceAudit(
                    engine=incoming.engine,
                    attempted=current.attempted + incoming.attempted,
                    hits=current.hits + incoming.hits,
                    failed=current.failed + incoming.failed,
                    filtered=current.filtered + incoming.filtered,
                    deduplicated=current.deduplicated + incoming.deduplicated,
                    fetch_failed=current.fetch_failed + incoming.fetch_failed,
                    retained=current.retained + incoming.retained,
                )
        return [merged[name] for name in sorted(merged)]

    async def search_only(self, topic: str) -> SearchResult:
        """两阶段 × 多引擎搜索，按 URL 去重（方法论 1.1 + P1 锚定/去锚）。

        Phase1（锚定）：纯 topic（+ 多轮模板 + extra_queries），不加 facets，
            窄查询锁定身份/核心信息。
        Phase2（去锚）：core_keyword(或 topic) + facets 维度展开，突破单一锚点。
            仅在设置了 core_keyword 或 facets 时启用。

        返回 SearchResult（hits + warnings）：搜索后端失败不再静默丢弃（修复 1）。
        """
        if self.config.llm_query_expansion and self.llm is not None:
            queries = await _llm_build_queries(
                topic, self.config.search_rounds, self.config.language, self.llm
            )
        else:
            queries = _build_queries(topic, self.config.search_rounds, self.config.language)
        # 用户显式查询置顶（点名要找的论文/方法，仅 Phase1，修正 4），保序去重
        if self.config.extra_queries:
            queries = list(dict.fromkeys(self.config.extra_queries + queries))
        # Phase2（去锚）：core_keyword 或 facets 触发；core 缺省回退 topic
        if self.config.core_keyword or self.config.facets:
            core = self.config.core_keyword or topic
            queries = list(dict.fromkeys(queries + _facet_queries(core, self.config.facets)))

        # 专家库/额外 URL 强档直注：绕过搜索与相关性过滤，置顶保证纳入（ExpertLib）。
        injected = self._direct_inject_hits(topic)
        # 专家弱档：org:<handle> <topic> 仅打 github 后端，结果 expert=True
        expert_gh, expert_warns = await self._expert_scoped_github_hits(topic)
        sr = await self.search_queries(queries)
        warnings = list(sr.warnings) + expert_warns

        head: list[SearchHit] = list(injected)
        # 弱档 org 命中紧随强档，仍标 expert；与搜索结果按 URL 去重
        seen = {h.url for h in head}
        for h in expert_gh:
            if h.url and h.url not in seen:
                head.append(h)
                seen.add(h.url)
        if not head:
            # 无专家注入时保持原 SearchResult 身份（向后兼容；仅附加弱档 warning）
            if expert_warns:
                return SearchResult(
                    hits=sr.hits, warnings=warnings, source_audits=sr.source_audits
                )
            return sr
        tail = [h for h in sr.hits if h.url not in seen]
        merged = (head + tail)[: self.config.max_total_results]
        return SearchResult(
            hits=merged, warnings=warnings, source_audits=sr.source_audits
        )

    def _direct_inject_hits(self, topic: str) -> list[SearchHit]:
        """强档直注 hit：config.extra_urls + 匹配专家的 seed_urls。

        这些是策展的确切 URL，直接成 hit（expert=True）跳过搜索，由 fetch 阶段照常抓取。
        experts_file 未设且 extra_urls 为空时返回空表，search_only 行为与原先一致。
        """
        official_urls = list(dict.fromkeys(self.config.official_urls))
        urls: list[str] = list(self.config.extra_urls)
        if self.config.experts_file:
            from ..experts import ExpertRegistry

            reg = ExpertRegistry.load(self.config.experts_file)
            matched = reg.match(topic, self.config.expert_match_min_overlap)
            urls += reg.seed_urls_for(matched)

        seen: set[str] = set()
        hits: list[SearchHit] = [
            SearchHit(
                url=url,
                title="",
                snippet="[official source]",
                source_engine="official",
                expert=True,
            )
            for url in official_urls
        ]
        seen.update(official_urls)
        for url in urls:
            if url and url not in seen:
                seen.add(url)
                hits.append(
                    SearchHit(
                        url=url,
                        title=url.rstrip("/").split("/")[-1] if "github.com" in url else "",
                        snippet="[expert/seed]",
                        source_engine="expert_seed",
                        expert=True,
                    )
                )
        return hits

    async def _expert_scoped_github_hits(
        self, topic: str
    ) -> tuple[list[SearchHit], list[str]]:
        """专家弱档：对匹配专家的 github handle 跑 ``org:X <topic>``（仅 GitHub 引擎）。

        不把 org: 查询塞进全引擎 search_queries，避免 web/openalex 被污染。
        需 search_engines 含 github 且 expert_scoped_github=True。
        """
        warnings: list[str] = []
        if not getattr(self.config, "expert_scoped_github", True):
            return [], warnings
        if not self.config.experts_file:
            return [], warnings
        engines = [str(e) for e in (self.config.search_engines or [])]
        if "github" not in engines:
            return [], warnings

        from ..experts import ExpertRegistry
        from ..search.github_backend import GitHubBackend

        reg = ExpertRegistry.load(self.config.experts_file)
        matched = reg.match(topic, self.config.expert_match_min_overlap)
        scoped = reg.scoped_queries_for(matched, topic)
        if not scoped:
            return [], warnings

        gh = GitHubBackend(
            self.config.github_token,
            enable_code_search=bool(getattr(self.config, "github_code_search", True)),
        )
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for engine, q in scoped:
            if engine != "github":
                continue
            try:
                batch = await gh.search(q, self.config.max_results_per_engine)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"expert scoped github 失败 query={q!r}: {e}")
                continue
            for h in batch:
                if not h.url or h.url in seen:
                    continue
                seen.add(h.url)
                hits.append(
                    SearchHit(
                        url=h.url,
                        title=h.title,
                        snippet=(h.snippet or "") + "\n[expert/scoped]",
                        source_engine=h.source_engine or "github",
                        expert=True,
                    )
                )
        if hits:
            logger.info("专家弱档 github 命中 %d 条（topic=%r）", len(hits), topic[:60])
        return hits, warnings

    def _deep_combos(self) -> list[tuple[str | None, int]]:
        """Deep-Search 的 (sort, offset) 矩阵：deep_pages 页 × deep_sorts 排序。

        relevance 直接传给后端（openalex/crossref 映射为默认相关性，arxiv/s2/pubmed
        亦回退默认），date/citations 走各源原生或客户端排序。offset 按页大小递增。
        """
        page_size = self.config.max_results_per_engine
        sorts = self.config.deep_sorts or ["relevance"]
        return [(s, p * page_size) for p in range(self.config.deep_pages) for s in sorts]

    async def search_queries(  # noqa: PLR0915 - search funnel records every terminal path
        self,
        queries: list[str],
        *,
        from_year: int | None = None,
        to_year: int | None = None,
        sort: str | None = None,
        offset: int | None = None,
    ) -> SearchResult:
        """对给定查询列表跑全部引擎，受 max_concurrent_searches 限流（修复 2），
        失败收集为 warnings（修复 1），按 URL 去重。Deepen 阶段也复用本方法。

        P2 时间标签：年份窗口缺省取 config.from_year/to_year（显式传参可覆盖）。
        P2 deep-search：显式 sort/offset → 单次（供画像时间线回溯）；否则
        config.deep_search 开启时展开 (sort,offset) 矩阵，每查询多次搜索后去重。"""
        if not self._proxy_preflight_done:
            proxy_result = await preflight_proxy(self.config.proxy)
            if not proxy_result.ok:
                raise CollectError(proxy_result.message)
            self._proxy_preflight_done = True

        # 按 config.proxy 设置模块级默认代理，供走 _http 的后端及 ddgs/tavily 共用
        set_default_proxy(self.config.proxy)

        # X 源 preflight：CLI 缺失/未登录时提前给出安装说明（不拖到中段）
        early_warnings: list[str] = []
        engines = [str(e) for e in (self.config.search_engines or [])]
        if any(e in ("x", "twitter") for e in engines):
            from ..search.x_backend import preflight_x

            pf = preflight_x(self.config, run_doctor=True)
            if not pf.ok:
                early_warnings.append(pf.message)
                logger.warning("X preflight 失败，将跳过 x/twitter 引擎: %s", pf.message[:200])
                # 从本轮引擎列表剔除，避免每个 query 重复 SearchError
                self._x_preflight_failed = True  # type: ignore[attr-defined]
            else:
                self._x_preflight_failed = False  # type: ignore[attr-defined]
                logger.info("%s", pf.message)

        fy = from_year if from_year is not None else self.config.from_year
        ty = to_year if to_year is not None else self.config.to_year
        if sort is not None or offset is not None:
            combos: list[tuple[str | None, int]] = [(sort, offset or 0)]  # 显式单次
        elif self.config.deep_search:
            combos = self._deep_combos()
        else:
            combos = [(None, 0)]

        sem = asyncio.Semaphore(self.config.max_concurrent_searches)

        async def _one(backend, query: str, s: str | None, off: int) -> list[SearchHit]:
            async with sem:
                return await backend.search(
                    query,
                    self.config.max_results_per_engine,
                    self.config.language,
                    from_year=fy,
                    to_year=ty,
                    sort=s,
                    offset=off,
                )

        tasks = []
        meta: list[tuple[str, str]] = []  # (engine, query)，用于失败时定位
        audits: dict[str, SourceAudit] = {
            str(engine): SourceAudit(engine=str(engine)) for engine in self.config.search_engines
        }
        skip_x = bool(getattr(self, "_x_preflight_failed", False))
        for engine in self.config.search_engines:
            if skip_x and str(engine) in ("x", "twitter"):
                continue
            backend = get_backend(engine, self.config)
            for query in queries:
                for s, off in combos:
                    meta.append((engine, query))
                    tasks.append(_one(backend, query, s, off))
                    name = str(engine)
                    audits[name] = self._increment(audits[name], attempted=1)
        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        hits: list[SearchHit] = []
        warnings: list[str] = list(early_warnings)
        seen: set[str] = set()
        for (engine, query), res in zip(meta, results):
            if isinstance(res, BaseException):
                warnings.append(f"{engine} 搜索失败 query={query!r}: {res}")
                name = str(engine)
                audits[name] = self._increment(audits[name], failed=1)
                continue
            name = str(engine)
            audits[name] = self._increment(audits[name], hits=len(res))
            for hit in res:
                threshold = self.config.search_relevance_min_overlap
                if threshold > 0 and _hit_relevance(query, hit) < threshold:
                    warnings.append(f"{engine} 低相关命中已跳过 query={query!r}: {hit.title[:80]}")
                    audits[name] = self._increment(audits[name], filtered=1)
                    continue
                if hit.url in seen:
                    audits[name] = self._increment(audits[name], deduplicated=1)
                    continue
                seen.add(hit.url)
                if len(hits) < self.config.max_total_results:
                    hits.append(hit.model_copy(update={"audit_engine": name}))
                else:
                    audits[name] = self._increment(audits[name], filtered=1)
        source_audits = [audits[name] for name in sorted(audits)]
        self._pending_source_audits = source_audits
        return SearchResult(
            hits=hits,
            warnings=warnings,
            source_audits=source_audits,
        )

    async def run(self, topic: str, work_dir: Path, *, dry_run: bool = False) -> CollectResult:
        raw_dir = ensure_dir(Path(work_dir) / "raw")
        if dry_run and self.config.official_urls:
            return CollectResult(
                files=[],
                sources=[],
                raw_dir=raw_dir,
                warnings=[
                    "dry-run 未抓取验证官方来源，已跳过 OpenAlex/Crossref/arXiv 扩展检索"
                ],
            )
        official_result = CollectResult(files=[], sources=[], raw_dir=raw_dir, warnings=[])
        if self.config.official_urls and not dry_run:
            try:
                for url in self.config.official_urls:
                    assert_safe_url(url)
            except UrlBlockedError as exc:
                raise CollectError("官方来源 URL 未通过安全校验，已停止学术扩展") from exc
            official_hits = [
                SearchHit(
                    url=url,
                    title="",
                    snippet="[official source]",
                    source_engine="official",
                    expert=True,
                )
                for url in dict.fromkeys(self.config.official_urls)
            ]
            self._pending_source_audits = [
                SourceAudit(
                    engine="official",
                    attempted=len(official_hits),
                    hits=len(official_hits),
                )
            ]
            official_result = await self.fetch_and_store(topic, official_hits, raw_dir)
            recorded_urls = {
                url
                for source in self._load_sources(raw_dir)
                if isinstance((url := source.get("url")), str)
            }
            missing = [hit.url for hit in official_hits if hit.url not in recorded_urls]
            successful_count = max(
                len(official_hits) - len(missing),
                len(official_result.files),
            )
            if successful_count == 0:
                details = "; ".join(official_result.warnings) or "未生成有效正文"
                raise CollectError(f"官方来源抓取失败，已停止学术扩展：{details}")
            if successful_count < len(official_hits):
                raise CollectError(
                    f"官方来源未全部抓取成功（{len(official_hits) - successful_count} 个），"
                    "已停止学术扩展"
                )

        sr = await self.search_only(topic)

        if dry_run:
            return CollectResult(files=[], sources=[], raw_dir=raw_dir, warnings=sr.warnings)

        extension_result = await self.fetch_and_store(topic, sr.hits, raw_dir)
        return CollectResult(
            files=[*official_result.files, *extension_result.files],
            sources=[*official_result.sources, *extension_result.sources],
            raw_dir=raw_dir,
            warnings=[*official_result.warnings, *sr.warnings, *extension_result.warnings],
            # extension_result 已从 source-audit.json 合并了 official 计数。
            source_audits=extension_result.source_audits,
        )

    async def fetch_and_store(  # noqa: PLR0915 - fetch funnel records every terminal path
        self,
        topic: str,
        hits: list[SearchHit],
        raw_dir: Path,
        *,
        source_audits: list[SourceAudit] | None = None,
    ) -> CollectResult:
        """抓取 hits 并写入 raw/，幂等且防覆盖。

        - 按 sources.json 已记录的 URL 去重（同一 URL 不重复抓取/写入）
        - 文件名 idx 从现有文件数续编、并加 URL 短哈希后缀，避免多次调用
          （含 Deepen 复用本方法）相互覆盖（修复 3 / 风险 9）
        - 写完后合并 sources.json（风险 8：来源不断裂）
        """
        ensure_dir(raw_dir)
        existing_urls = {
            url
            for source in self._load_sources(raw_dir)
            if isinstance((url := source.get("url")), str)
        }
        pending = source_audits
        if pending is None:
            pending = list(getattr(self, "_pending_source_audits", []))
            self._pending_source_audits = []
        audits = {audit.engine: audit for audit in pending}
        for hit in hits:
            name = hit.audit_engine or hit.source_engine or "unknown"
            audits.setdefault(name, SourceAudit(engine=name))
            if hit.url in existing_urls:
                audits[name] = self._increment(audits[name], deduplicated=1)
        todo = [h for h in hits if h.url not in existing_urls]

        fetcher = Fetcher(
            timeout_sec=self.config.timeout_sec,
            parse_pdf=self.config.parse_pdf,
            mineru_cmd=self.config.mineru_cmd,
            pdf_dir=raw_dir / "_pdfs",
            proxy=self.config.proxy,
        )
        sem = asyncio.Semaphore(self.config.concurrency)

        async def _fetch(hit: SearchHit) -> tuple[SearchHit, FetchResult]:
            async with sem:
                # 视频 URL：Crawl4AI 抓到的是播放器壳页，无实质正文。写 discovery
                # 笔记让 youtube/bilibili 搜索命中进入 raw/（完整 whisper 转写走 VideoIngest）。
                if _is_video_url(hit.url):
                    body = (
                        f"# {hit.title or 'Video'}\n\n"
                        f"- URL: {hit.url}\n"
                        f"- engine: {hit.source_engine}\n"
                        "- note: video discovery "
                        "(not transcribed; use --video-url for full ingest)\n\n"
                        f"{hit.snippet or ''}\n"
                    )
                    fr = FetchResult(hit.url, body, ok=True)
                    return hit, fr
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
            name = hit.audit_engine or hit.source_engine or "unknown"
            if not fr.ok or not fr.markdown.strip():
                audits[name] = self._increment(audits[name], fetch_failed=1)
                continue
            # 垃圾过滤：正文过短(登录页/导航页/JS空壳)直接丢弃
            if len(fr.markdown.strip()) < self.config.min_doc_chars:
                audits[name] = self._increment(audits[name], filtered=1)
                continue
            if hit.url in written_urls:  # 二级链接也可能撞已有 URL
                audits[name] = self._increment(audits[name], deduplicated=1)
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
                    snippet=hit.snippet,
                )
            )
            audits[name] = self._increment(audits[name], retained=1)

        self._append_sources(raw_dir, sources)
        merged_audits = self._persist_audits(raw_dir, list(audits.values()))
        return CollectResult(
            files=files,
            sources=sources,
            raw_dir=raw_dir,
            source_audits=merged_audits,
        )

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

    def _persist_audits(self, raw_dir: Path, audits: list[SourceAudit]) -> list[SourceAudit]:
        path = raw_dir / "source-audit.json"
        previous: list[SourceAudit] = []
        if path.exists():
            try:
                payload = read_json(path)
                previous = [SourceAudit.model_validate(item) for item in payload.get("sources", [])]
            except (OSError, ValueError, AttributeError):
                previous = []
        merged = self._merge_audits(previous, audits)
        write_json(path, {"version": 1, "sources": [item.model_dump() for item in merged]})
        return merged

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
            # SSRF: skip private hrefs before fetch (never call fetcher.fetch on them)
            try:
                assert_safe_url(url)
            except UrlBlockedError:
                logger.warning("SSRF guard skipped secondary link: %s", hash_url(url))
                return None
            async with sem:
                fr = await fetcher.fetch(url)
                hit = SearchHit(url=url, title="", source_engine="secondary")
                return hit, fr

        if not targets:
            return []
        results = await asyncio.gather(*[_f(u) for u in targets])
        return [r for r in results if r is not None]


async def collect(topic: str, config: CollectorConfig, work_dir: Path) -> CollectResult:
    """模块级函数（01 §2.2 签名）。"""
    return await Collector(config).run(topic, work_dir)
