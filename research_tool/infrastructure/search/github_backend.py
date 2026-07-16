"""GitHub 仓库搜索后端（免费 REST API）。

技术调研必备：开源实现、README/文档、社区活跃度（stars/forks）。
API: https://api.github.com/search/repositories
可选（需 token）: https://api.github.com/search/code

质量策略（P0）：
1. 查询变体：全词 + 短关键词（避免 GitHub AND 过严漏权威仓）
2. 候选池：best match（不设 sort）+ sort=updated 双路合并后再复合分
3. 复合分：stars / recency / desc / fork 比 / topic 词重叠
4. 可选 code search（有 GITHUB_TOKEN 时）补「代码里出现关键词」的仓

无 Key 时搜索限 10 次/分钟；配置 github_token 可提升配额。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ...common.logging_config import get_logger
from ...domain.errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

logger = get_logger(__name__)

_ENDPOINT = "https://api.github.com/search/repositories"
_CODE_ENDPOINT = "https://api.github.com/search/code"
_WORD_RE = re.compile(r"[a-z0-9]+")

# 复合分权重
# 提高 topic 权重：多路合并后高星无关仓不应压过 query 贴合的官方实现
_W_STARS = 0.25
_W_RECENCY = 0.20
_W_DESC = 0.10
_W_FORK_RATIO = 0.10
_W_TOPIC = 0.35

# 查询变体时丢掉的极泛词
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "using",
        "based",
        "via",
        "a",
        "an",
        "of",
        "in",
        "on",
        "to",
        "or",
        "vs",
        "survey",
        "overview",
        "review",
        "paper",
        "code",
        "repo",
        "github",
        "implementation",
        "model",
        "models",
        "method",
        "methods",
        "learning",
        "deep",
        "neural",
        "network",
        "networks",
        # 过泛技术词：单独作 query 会淹没 three.js/godot 等无关高星仓
        "3d",
        "2d",
        "ai",
        "ml",
        "cv",
        "nlp",
        "rl",
        "gpu",
        "cpu",
        "api",
        "app",
        "src",
        "lib",
        "data",
        "vision",
        "image",
        "video",
        "audio",
        "text",
        "graph",
        "point",
        "cloud",
    }
)


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower().replace("-", " ").replace("_", " ")))


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recency_score(pushed_at: str | None) -> float:
    """0–1：推送越近越高。"""
    dt = _parse_iso(pushed_at)
    if dt is None:
        return 0.0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = max((now - dt).total_seconds() / 86400.0, 0.0)
    return math.exp(-days / 180.0)


def _composite_score(repo: dict, query: str) -> float:
    stars = float(repo.get("stargazers_count") or 0)
    forks = float(repo.get("forks_count") or 0)
    desc = repo.get("description") or ""
    name = repo.get("full_name") or repo.get("name") or ""
    topics = repo.get("topics") or []
    if not isinstance(topics, list):
        topics = []

    s_stars = math.log1p(stars) / math.log1p(100_000)
    s_stars = min(max(s_stars, 0.0), 1.5)
    s_recency = _recency_score(repo.get("pushed_at") or repo.get("updated_at"))
    s_desc = 1.0 if (desc and len(desc.strip()) >= 20) else (0.4 if desc else 0.0)
    if repo.get("homepage"):
        s_desc = min(s_desc + 0.2, 1.0)

    if stars > 0:
        ratio = forks / stars
        if ratio < 0.01:
            s_fork = 0.3
        elif ratio > 2.0:
            s_fork = 0.2
        else:
            s_fork = min(ratio * 2.0, 1.0)
    else:
        s_fork = 0.5 if forks == 0 else 0.3

    q_tok = _tokens(query)
    doc_tok = _tokens(f"{name} {desc} {' '.join(str(t) for t in topics)}")
    s_topic = (len(q_tok & doc_tok) / len(q_tok)) if q_tok and doc_tok else 0.0

    return (
        _W_STARS * s_stars
        + _W_RECENCY * s_recency
        + _W_DESC * s_desc
        + _W_FORK_RATIO * s_fork
        + _W_TOPIC * s_topic
    )


def query_variants(query: str, *, max_variants: int = 4) -> list[str]:
    """全词 + 短关键词变体，缓解 GitHub 多词 AND 漏权威仓。

    按**原文词序**抽显著 token（而非按长度），优先产品缩写：
    例：\"vggt 3d reconstruction geometry\" →
      full, \"vggt\", \"vggt reconstruction\", \"vggt geometry\"。
    """
    q = (query or "").strip()
    if not q:
        return []
    out: list[str] = [q]
    # 保序显著词：len>=3 且非停用；另允许 2 字符但含数字的 token（如 v2）
    ordered: list[str] = []
    for m in _WORD_RE.finditer(q.lower().replace("-", " ").replace("_", " ")):
        t = m.group(0)
        if t in _STOP:
            continue
        if len(t) >= 3 or (len(t) == 2 and any(c.isdigit() for c in t)):
            if t not in ordered:
                ordered.append(t)
    if not ordered:
        return out
    seen_l = {q.lower()}
    # 单独：前两个显著词各成一查询（vggt 优先于 reconstruction）
    for t in ordered[:2]:
        if t not in seen_l:
            out.append(t)
            seen_l.add(t)
    # 双词：第 1+2、第 1+3
    if len(ordered) >= 2:
        pair = f"{ordered[0]} {ordered[1]}"
        if pair not in seen_l:
            out.append(pair)
            seen_l.add(pair)
    if len(ordered) >= 3 and len(out) < max_variants:
        pair2 = f"{ordered[0]} {ordered[2]}"
        if pair2 not in seen_l:
            out.append(pair2)
            seen_l.add(pair2)
    return out[:max_variants]


def _repo_to_hit(repo: dict, score: float, *, source_engine: str = "github") -> SearchHit | None:
    url = repo.get("html_url")
    if not url:
        return None
    stars = repo.get("stargazers_count", 0)
    forks = repo.get("forks_count", 0)
    lang = repo.get("language") or ""
    desc = repo.get("description") or ""
    topics = repo.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    homepage = repo.get("homepage") or ""
    meta = f"★{stars} ⑂{forks}" + (f" | {lang}" if lang else "")
    meta += f" | score={score:.2f}"
    extra_bits: list[str] = []
    if topics:
        extra_bits.append("topics: " + ", ".join(str(t) for t in topics[:8]))
    if homepage:
        extra_bits.append(f"homepage: {homepage}")
    snippet = f"{meta}\n{desc}".strip()
    if extra_bits:
        snippet = snippet + "\n" + "\n".join(extra_bits)
    return SearchHit(
        url=url,
        title=repo.get("full_name", ""),
        snippet=snippet,
        source_engine=source_engine,
    )


class GitHubBackend(SearchBackend):
    name = "github"

    def __init__(
        self,
        token: str | None = None,
        *,
        enable_code_search: bool = True,
        max_query_variants: int = 4,
    ) -> None:
        self.token = (token or "").strip() or None
        # code search 强制要 token；无 token 时静默跳过
        self.enable_code_search = bool(enable_code_search and self.token)
        self.max_query_variants = max(1, min(int(max_query_variants), 6))

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _search_repos_once(
        self,
        query: str,
        *,
        per_page: int,
        sort: str | None,
    ) -> list[dict]:
        params: dict = {
            "q": query,
            "per_page": min(max(per_page, 1), 30),
        }
        if sort:
            params["sort"] = sort
            params["order"] = "desc"
        # sort is None → GitHub best match（质量发现主路）
        try:
            data = await get_json(_ENDPOINT, params=params, headers=self._headers())
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"github 搜索失败: {describe(e)}") from e
        items = (data.get("items") or []) if isinstance(data, dict) else []
        return [r for r in items if isinstance(r, dict) and r.get("html_url")]

    async def _search_code_repos(self, query: str, max_repos: int) -> list[dict]:
        """用 code search 找「代码里出现关键词」的仓库；返回精简 repo dict。"""
        if not self.enable_code_search:
            return []
        # 缩短 query 防 422
        q = " ".join(list(_tokens(query))[:4])
        if not q:
            return []
        params = {
            "q": f"{q} in:file",
            "per_page": min(max(max_repos * 2, 5), 30),
        }
        try:
            data = await get_json(_CODE_ENDPOINT, params=params, headers=self._headers())
        except Exception as e:  # noqa: BLE001
            # code search 失败不拖垮主搜索
            logger.warning("github code search 跳过: %s", describe(e))
            return []
        items = (data.get("items") or []) if isinstance(data, dict) else []
        repos: list[dict] = []
        seen: set[str] = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            repo = it.get("repository") or {}
            if not isinstance(repo, dict):
                continue
            url = repo.get("html_url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            # code search 的 repository 对象字段较少，补默认
            repos.append(
                {
                    "html_url": url,
                    "full_name": repo.get("full_name") or "",
                    "description": repo.get("description")
                    or f"[code-search hit] {it.get('name', '')}",
                    "stargazers_count": repo.get("stargazers_count") or 0,
                    "forks_count": repo.get("forks_count") or 0,
                    "language": repo.get("language"),
                    "pushed_at": repo.get("pushed_at"),
                    "updated_at": repo.get("updated_at"),
                    "homepage": repo.get("homepage"),
                    "topics": repo.get("topics") or [],
                    "_from_code_search": True,
                }
            )
            if len(repos) >= max_repos:
                break
        return repos

    async def search(
        self, query: str, max_results: int, language: str = "both", **_kw
    ) -> list[SearchHit]:
        # **_kw 吸收 P2 时间/排序/分页：本源自建多路候选，忽略服务端单 sort
        q = (query or "").strip()
        if not q:
            return []

        variants = query_variants(q, max_variants=self.max_query_variants)
        per_page = min(max(max_results * 2, max_results), 30)
        # 双路 sort：None=best match，updated=新仓进池
        sort_paths: list[str | None] = [None, "updated"]

        by_url: dict[str, dict] = {}
        errors: list[str] = []
        for vq in variants:
            for sort in sort_paths:
                try:
                    items = await self._search_repos_once(vq, per_page=per_page, sort=sort)
                except SearchError as e:
                    errors.append(str(e))
                    continue
                for repo in items:
                    url = repo["html_url"]
                    prev = by_url.get(url)
                    if prev is None:
                        by_url[url] = repo
                    else:
                        # 保留 stars 更高 / 描述更长的副本
                        if (repo.get("stargazers_count") or 0) > (
                            prev.get("stargazers_count") or 0
                        ):
                            by_url[url] = repo
                        elif not (prev.get("description") or "") and (
                            repo.get("description") or ""
                        ):
                            by_url[url] = repo

        # P1：有 token 时 code search 补仓
        if self.enable_code_search:
            for repo in await self._search_code_repos(q, max_repos=max(max_results, 5)):
                url = repo["html_url"]
                if url not in by_url:
                    by_url[url] = repo

        if not by_url and errors:
            raise SearchError(errors[0])

        scored: list[tuple[float, dict]] = []
        for repo in by_url.values():
            scored.append((_composite_score(repo, q), repo))
        scored.sort(key=lambda t: t[0], reverse=True)

        hits: list[SearchHit] = []
        for score, repo in scored[:max_results]:
            eng = "github_code" if repo.get("_from_code_search") else "github"
            hit = _repo_to_hit(repo, score, source_engine=eng)
            if hit:
                hits.append(hit)
        return hits
