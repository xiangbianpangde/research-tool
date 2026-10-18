"""Stage 1: Collector —— 搜索 + 抓取。

依据 01 §2、02 §2、05 §3 Stage1。
流程：多引擎搜索 → URL 去重 → 并行抓取（Crawl4AI）→ 写 raw/XX-{domain}.md
+ sources.json。depth 控制抓取深度（1=仅搜索 2=抓结果页 3=追二级链接）。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ...domain.models import CollectorConfig, CollectResult, Source, SourceAudit
from ..search import get_backend
from ..search._http import reset_request_deadline, set_default_proxy, set_request_deadline
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
from ...common.url_guard import (
    UrlGuardBusyError,
    UrlGuardTimeoutError,
    assert_safe_url_async,
)
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

    async def _assert_safe_url_bounded(self, url: str) -> None:
        timeout = max(float(self.config.timeout_sec), 0.001)
        try:
            await asyncio.wait_for(
                assert_safe_url_async(url, timeout_sec=timeout), timeout=timeout,
            )
        except UrlGuardTimeoutError:
            raise
        except asyncio.TimeoutError as exc:
            raise UrlGuardTimeoutError("Collector DNS guard deadline exceeded") from exc

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
            timeout = float(self.config.timeout_sec)
            deadline = asyncio.get_running_loop().time() + timeout
            token = set_request_deadline(deadline)
            try:
                # Keep the historical two-positional-argument seam: the
                # backend's optional deep-search kwargs are not needed here.
                batch = await asyncio.wait_for(
                    gh.search(q, self.config.max_results_per_engine), timeout=timeout
                )
            except asyncio.TimeoutError:
                warnings.append(
                    f"expert scoped github 搜索截止时间超时（>{self.config.timeout_sec:g}s）"
                    f" query={q!r}"
                )
                continue
            except Exception as e:  # noqa: BLE001
                warnings.append(f"expert scoped github 失败 query={q!r}: {e}")
                continue
            finally:
                reset_request_deadline(token)
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

    def _search_request_budget(self, total_combinations: int, baseline_requests: int = 0) -> int:
        """Bound deep expansion without starving a configured engine/query pair.

        For N total combinations, E active engines and Q queries, reserve one
        first-combo baseline sweep E*Q.  Then allow useful result capacity plus
        an in-flight wave: B=min(N, max(E*Q, ceil(M/P)+C-1)), where M is
        max_total_results, P max_results_per_engine and C search concurrency.
        """
        if total_combinations <= 0:
            return 0
        useful_waves = math.ceil(
            self.config.max_total_results / self.config.max_results_per_engine
        )
        return min(
            total_combinations,
            max(baseline_requests, useful_waves + self.config.max_concurrent_searches - 1),
        )

    async def _search_backend_request(
        self,
        backend,
        query: str,
        *,
        from_year: int | None,
        to_year: int | None,
        sort: str | None,
        offset: int,
        semaphore: asyncio.Semaphore,
    ) -> list[SearchHit]:
        """Run one backend call under both concurrency and one total deadline."""
        async with semaphore:
            timeout = float(self.config.timeout_sec)
            deadline = asyncio.get_running_loop().time() + timeout
            token = set_request_deadline(deadline)
            try:
                return await asyncio.wait_for(
                    backend.search(
                        query,
                        self.config.max_results_per_engine,
                        self.config.language,
                        from_year=from_year,
                        to_year=to_year,
                        sort=sort,
                        offset=offset,
                    ),
                    timeout=timeout,
                )
            finally:
                reset_request_deadline(token)

    async def search_queries(  # noqa: PLR0915 - search funnel records every terminal path
        self,
        queries: list[str],
        *,
        from_year: int | None = None,
        to_year: int | None = None,
        sort: str | None = None,
        offset: int | None = None,
    ) -> SearchResult:
        """对给定查询列表跑全部引擎，受并发、请求预算和单请求截止时间约束。

        P2 时间标签：年份窗口缺省取 config.from_year/to_year（显式传参可覆盖）。
        P2 deep-search：显式 sort/offset → 单次（供画像时间线回溯）；否则
        config.deep_search 开启时展开 (sort,offset) 矩阵，每查询多次搜索后去重。
        """
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
        planned: list[tuple[str, str, object, str | None, int]] = []
        audits: dict[str, SourceAudit] = {
            str(engine): SourceAudit(engine=str(engine)) for engine in self.config.search_engines
        }
        skip_x = bool(getattr(self, "_x_preflight_failed", False))
        active_engines: list[tuple[str, object]] = []
        for engine in self.config.search_engines:
            name = str(engine)
            if skip_x and name in ("x", "twitter"):
                continue
            active_engines.append((name, get_backend(engine, self.config)))
        # Query order is deliberate: callers' explicit queries are already
        # placed first by search_only; the first combo then sweeps every
        # query×engine pair before deep pages/sorts are expanded.
        for s, off in combos:
            for query in queries:
                for name, backend in active_engines:
                    planned.append((name, query, backend, s, off))

        baseline_requests = len(active_engines) * len(queries)
        request_budget = self._search_request_budget(len(planned), baseline_requests)
        selected = planned[:request_budget]
        warnings: list[str] = list(early_warnings)
        if request_budget < len(planned):
            warnings.append(
                "搜索请求预算已截断："
                f"planned={len(planned)}, budget={request_budget}, "
                f"baseline={baseline_requests}, "
                "formula=max(engine*query,ceil(max_total_results/max_results_per_engine)+"
                "max_concurrent_searches-1)"
            )

        hits: list[SearchHit] = []
        seen: set[str] = set()
        width = self.config.max_concurrent_searches
        for start in range(0, len(selected), width):
            # Build at most one concurrency wave; do not queue the remaining
            # combinations after retained capacity has already been reached.
            wave = selected[start : start + width]
            tasks: list[asyncio.Task] = []
            for engine, query, backend, s, off in wave:
                tasks.append(
                    asyncio.create_task(
                        self._search_backend_request(
                            backend,
                            query,
                            from_year=fy,
                            to_year=ty,
                            sort=s,
                            offset=off,
                            semaphore=sem,
                        )
                    )
                )
                audits[engine] = self._increment(audits[engine], attempted=1)
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            for (engine, query, _backend, _sort, _offset), res in zip(wave, results):
                name = str(engine)
                if isinstance(res, asyncio.CancelledError):
                    raise res
                if isinstance(res, BaseException):
                    if isinstance(res, asyncio.TimeoutError):
                        detail = f"搜索截止时间超时（>{self.config.timeout_sec:g}s）"
                    else:
                        detail = str(res) or type(res).__name__
                    warnings.append(f"{engine} 搜索失败 query={query!r}: {detail}")
                    audits[name] = self._increment(audits[name], failed=1)
                    continue
                audits[name] = self._increment(audits[name], hits=len(res))
                for hit in res:
                    threshold = self.config.search_relevance_min_overlap
                    if threshold > 0 and _hit_relevance(query, hit) < threshold:
                        warnings.append(
                            f"{engine} 低相关命中已跳过 query={query!r}: {hit.title[:80]}"
                        )
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
            completed_requests = start + len(wave)
            if (
                len(hits) >= self.config.max_total_results
                and completed_requests >= baseline_requests
                and completed_requests < len(selected)
            ):
                warnings.append("搜索结果已达保留上限且基线覆盖完成；未启动剩余请求组合")
                break
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
                    await self._assert_safe_url_bounded(url)
            except UrlBlockedError as exc:
                raise CollectError("官方来源 URL 未通过安全校验，已停止学术扩展") from exc
            except (UrlGuardTimeoutError, UrlGuardBusyError) as exc:
                raise CollectError("官方来源 DNS 校验超时或容量已满，已停止学术扩展") from exc
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
            # Only the required official primary URLs satisfy this gate.  Files
            # produced by depth-3 secondary links must never count as primary
            # success, even when they increase ``official_result.files``.
            successful_count = len(official_hits) - len(missing)
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
        - 文件名 idx 从现有最大序号续编，按输入顺序预留、加 URL 短哈希后缀，
          避免完成顺序或部分取消后的重试覆盖既有文件
        - 每个完成项同步写 raw、sources.json 和对应 audit 增量；取消/异常继续抛出，
          不把持久化部分成果当作 dispatcher 的阶段成功提交
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
            originals_dir=(raw_dir / "_originals") if self.config.save_originals else None,
            arxiv_fulltext=self.config.arxiv_fulltext,
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
                    return hit, FetchResult(hit.url, body, ok=True)
                if self.config.depth >= 2:
                    fr = await fetcher.fetch(hit.url)
                else:
                    # depth==1：不抓取，用搜索摘要作为内容
                    fr = FetchResult(hit.url, hit.snippet, ok=bool(hit.snippet))
                return hit, fr

        files: list[Path] = []
        sources: list[Source] = []
        warnings: list[str] = []
        base_idx = self._next_idx(raw_dir)
        written_urls: set[str] = set(existing_urls)
        # Reserve slots by input order.  Completion order may vary, but a URL's
        # deterministic index does not depend on which sibling returns first.
        slots = {id(hit): base_idx + position + 1 for position, hit in enumerate(todo)}

        # Persist search attempts and already-known URL deduplication before any
        # fetch begins.  Subsequent calls persist only deltas, avoiding double
        # counting while keeping source-audit.json valid during cancellation.
        self._append_sources(raw_dir, [])
        merged_audits = self._persist_audits(raw_dir, list(audits.values()))

        def _persist_one(hit: SearchHit, fr: FetchResult, slot: int) -> None:
            """Persist one completed fetch and its matching audit delta."""
            nonlocal merged_audits
            name = hit.audit_engine or hit.source_engine or "unknown"
            audits.setdefault(name, SourceAudit(engine=name))
            if not fr.ok or not fr.markdown.strip():
                detail = fr.error or "未返回正文"
                warnings.append(f"抓取失败 url_hash={hash_url(hit.url)}: {detail}")
                audits[name] = self._increment(audits[name], fetch_failed=1)
                merged_audits = self._persist_audits(
                    raw_dir, [SourceAudit(engine=name, fetch_failed=1)]
                )
                return
            # 垃圾过滤：正文过短(登录页/导航页/JS空壳)直接丢弃
            if len(fr.markdown.strip()) < self.config.min_doc_chars:
                warnings.append(f"抓取正文过短，已过滤 url_hash={hash_url(hit.url)}")
                audits[name] = self._increment(audits[name], filtered=1)
                merged_audits = self._persist_audits(
                    raw_dir, [SourceAudit(engine=name, filtered=1)]
                )
                return
            if hit.url in written_urls:  # 二级链接也可能撞已有 URL
                audits[name] = self._increment(audits[name], deduplicated=1)
                merged_audits = self._persist_audits(
                    raw_dir, [SourceAudit(engine=name, deduplicated=1)]
                )
                return
            written_urls.add(hit.url)
            domain = domain_of(hit.url)
            fname = f"{slot:02d}-{safe_filename(domain)}-{_url_hash(hit.url)}.md"
            fpath = raw_dir / fname
            via = f'{hit.source_engine}_search("{topic}")'
            evidence_kind = (
                "discovery"
                if _is_video_url(hit.url)
                else "snippet"
                if self.config.depth == 1
                else "body"
            )
            header = (
                f"<!-- source: {hit.url} -->\n"
                f"<!-- title: {hit.title} -->\n"
                f"<!-- fetched: {_now_iso()} -->\n"
                f"<!-- evidence_kind: {evidence_kind} -->\n"
                f"<!-- via: {via} -->\n\n"
            )
            write_text(fpath, header + fr.markdown)
            source = Source(
                url=hit.url,
                title=hit.title,
                fetched_at=_now_iso(),
                source_engine=hit.source_engine,
                content_hash=_sha256(fr.markdown),
                snippet=hit.snippet,
            )
            self._append_sources(raw_dir, [source])
            files.append(fpath)
            sources.append(source)
            audits[name] = self._increment(audits[name], retained=1)
            merged_audits = self._persist_audits(raw_dir, [SourceAudit(engine=name, retained=1)])

        # Existing URLs are accounted for in the initial persisted audit delta.
        # New fetches are handled one completion at a time below.
        tasks = {
            asyncio.create_task(_fetch(hit)): hit
            for hit in todo
        }
        pending = set(tasks)
        fetched: list[tuple[SearchHit, FetchResult]] = []
        processed: set[asyncio.Task] = set()
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    processed.add(task)
                    hit = tasks[task]
                    try:
                        fetched_item = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - preserve non-success semantics
                        fetched_item = (hit, FetchResult(hit.url, "", ok=False, error=str(exc)))
                        fetched.append(fetched_item)
                        try:
                            _persist_one(*fetched_item, slots[id(hit)])
                        except Exception as persistence_error:  # noqa: BLE001
                            logger.warning(
                                "fetch failure audit flush failed url_hash=%s error_type=%s",
                                hash_url(hit.url), type(persistence_error).__name__,
                            )
                        raise
                    fetched.append(fetched_item)
                    _persist_one(*fetched_item, slots[id(hit)])
        except BaseException:
            # Best-effort flush must neither replace the original failure nor
            # prevent sibling cleanup when persistence itself is failing.
            try:
                for task in tasks:
                    if task.done() and task not in processed:
                        processed.add(task)
                        hit = tasks[task]
                        try:
                            fetched_item = task.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:  # noqa: BLE001
                            fetched_item = (hit, FetchResult(hit.url, "", ok=False, error=str(exc)))
                        fetched.append(fetched_item)
                        try:
                            _persist_one(*fetched_item, slots[id(hit)])
                        except Exception as flush_error:  # noqa: BLE001 - preserve original error
                            logger.warning(
                                "partial fetch flush failed url_hash=%s error_type=%s",
                                hash_url(hit.url), type(flush_error).__name__,
                            )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            raise

        # depth==3：对成功页追踪少量二级内部链接。  Initial pages are already
        # durable before this expansion starts, so a later cancellation cannot
        # erase their committed evidence.
        if self.config.depth >= 3 and fetched:
            extra = await self._follow_links(list(fetched), fetcher, sem, topic)
            next_slot = base_idx + len(todo) + 1
            for hit, fr in extra:
                _persist_one(hit, fr, next_slot)
                next_slot += 1

        return CollectResult(
            files=files,
            sources=sources,
            raw_dir=raw_dir,
            warnings=warnings,
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
        """Return the highest numeric raw prefix, preserving gaps after partial runs."""
        indexes = []
        for path in raw_dir.glob("*.md"):
            match = re.match(r"^(\d+)-", path.name)
            if match:
                indexes.append(int(match.group(1)))
        return max(indexes, default=0)

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
                # DNS validation shares the fetch slot; a link batch must not
                # exhaust resolver admission before its fetches can start.
                try:
                    await self._assert_safe_url_bounded(url)
                except UrlBlockedError:
                    logger.warning("SSRF guard skipped secondary link: %s", hash_url(url))
                    return None
                except (UrlGuardTimeoutError, UrlGuardBusyError) as exc:
                    logger.warning(
                        "secondary link DNS guard failed url_hash=%s error_type=%s",
                        hash_url(url), type(exc).__name__,
                    )
                    raise
                fr = await fetcher.fetch(url)
                hit = SearchHit(url=url, title="", source_engine="secondary")
                return hit, fr

        if not targets:
            return []
        tasks = [asyncio.create_task(_f(url)) for url in targets]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            # gather propagates a failed secondary immediately without
            # cancelling siblings.  Always cancel/reap them before leaving;
            # an original secondary error or caller cancellation still raises.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r is not None]


async def collect(topic: str, config: CollectorConfig, work_dir: Path) -> CollectResult:
    """模块级函数（01 §2.2 签名）。"""
    return await Collector(config).run(topic, work_dir)
