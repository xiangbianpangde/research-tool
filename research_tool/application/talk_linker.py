"""TalkLinker：论文标题 → YouTube 演讲视频关联（CVPRTalk 阶段 F）。

流程（默认关，``TalkConfig.enabled`` / CLI ``--with-talks`` 开启）：
1. 从 ``raw/sources.json`` 抽论文候选（cvpr 源 / thecvf / arxiv 优先）
2. 对每篇用 YouTubeBackend 搜 ``<title> CVPR <year>``
3. 标题 token Jaccard 置信闸（默认 ≥0.7，宁漏不误）
4. 命中写 discovery 笔记到 raw/（``from_paper`` 元数据）；可选 ``ingest=True`` 全量转写

不阻塞主管道：搜索失败 / 无命中 / 低于阈值一律跳过并记入 report.warnings。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from ..common.logging_config import get_logger
from ..domain.models import TalkConfig
from ..infrastructure.search.base import SearchHit
from ..infrastructure.search.youtube_backend import YouTubeBackend
from ..infrastructure.stages.base import ensure_dir, write_text

logger = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_TITLE_HEADER_RE = re.compile(r"<!--\s*title:\s*(.*?)\s*-->", re.IGNORECASE)
_SOURCE_HEADER_RE = re.compile(r"<!--\s*source:\s*(.*?)\s*-->", re.IGNORECASE)

# 优先视为"论文"的源 / URL 标记
_PAPER_ENGINES = frozenset({"cvpr", "arxiv", "openalex", "crossref", "semantic_scholar", "scholar"})
_PAPER_URL_MARKERS = (
    "openaccess.thecvf.com",
    "arxiv.org",
    "doi.org",
    "ieee.org",
    "acm.org",
    "springer.com",
    "cvf",
)

# 官方 / 高可信频道关键词（prefer_official_channel）
_OFFICIAL_CHANNEL_HINTS = (
    "computer vision foundation",
    "cvpr",
    "iccv",
    "eccv",
    "thecvf",
    "cvf",
    "meta ai",
    "google research",
    "deepmind",
)


@dataclass(frozen=True)
class PaperCandidate:
    title: str
    url: str = ""
    year: int | None = None
    source_engine: str = ""


@dataclass(frozen=True)
class TalkMatch:
    paper_title: str
    video_url: str | None
    confidence: float
    matched: bool
    video_title: str = ""
    channel: str = ""
    year: int | None = None
    reason: str = ""


@dataclass
class TalkEnrichReport:
    """一次 talk enrichment 的结果摘要。"""

    candidates: int = 0
    matched: list[TalkMatch] = field(default_factory=list)
    skipped: list[TalkMatch] = field(default_factory=list)
    files_written: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ingested_urls: list[str] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower().replace("-", " ").replace("_", " ")))


def title_similarity(a: str, b: str) -> float:
    """归一化 token Jaccard：|A∩B| / |A∪B|，空集返回 0。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _parse_year(*texts: str) -> int | None:
    for t in texts:
        if not t:
            continue
        m = _YEAR_RE.search(t)
        if m:
            y = int(m.group(1))
            if 1990 <= y <= 2100:
                return y
    return None


def _is_paper_source(url: str, engine: str) -> bool:
    eng = (engine or "").lower()
    if eng in _PAPER_ENGINES:
        return True
    u = (url or "").lower()
    return any(m in u for m in _PAPER_URL_MARKERS)


def _channel_from_snippet(snippet: str) -> str:
    # YouTubeBackend snippet: "频道 | 时长 x | ..."
    if not snippet:
        return ""
    return snippet.split("|")[0].strip()


def _official_bonus(channel: str, video_title: str) -> float:
    blob = f"{channel} {video_title}".lower()
    return 0.08 if any(h in blob for h in _OFFICIAL_CHANNEL_HINTS) else 0.0


class TalkLinker:
    """论文 → talk 匹配器。"""

    def __init__(
        self,
        config: TalkConfig | None = None,
        youtube: YouTubeBackend | None = None,
    ) -> None:
        self.config = config or TalkConfig()
        self.youtube = youtube or YouTubeBackend()

    # ------------------------------------------------------------------ #
    # 候选论文
    # ------------------------------------------------------------------ #
    def candidates_from_sources(self, raw_dir: Path) -> list[PaperCandidate]:
        """从 sources.json 抽论文候选，去重保序。"""
        path = Path(raw_dir) / "sources.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取 sources.json 失败: %s", e)
            return []
        if not isinstance(raw, list):
            return []

        seen: set[str] = set()
        out: list[PaperCandidate] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            engine = (item.get("source_engine") or "").strip()
            snippet = item.get("snippet") or ""
            if not title or len(title) < 8:
                continue
            if not _is_paper_source(url, engine):
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                PaperCandidate(
                    title=title,
                    url=url,
                    year=_parse_year(snippet, url, title),
                    source_engine=engine,
                )
            )
        return out

    def candidates_from_clean_headers(self, clean_or_raw: Path) -> list[PaperCandidate]:
        """回退：从 clean/raw 的 HTML 注释头解析 title + source。"""
        d = Path(clean_or_raw)
        if not d.is_dir():
            return []
        seen: set[str] = set()
        out: list[PaperCandidate] = []
        for f in sorted(d.glob("*.md")):
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:800]
            except OSError:
                continue
            tm = _TITLE_HEADER_RE.search(head)
            sm = _SOURCE_HEADER_RE.search(head)
            title = (tm.group(1).strip() if tm else "")
            url = (sm.group(1).strip() if sm else "")
            if not title or len(title) < 8:
                continue
            if url and not _is_paper_source(url, ""):
                # 无引擎时仅靠 URL；无 URL 则跳过（避免把笔记标题当论文）
                continue
            if not url:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(PaperCandidate(title=title, url=url, year=_parse_year(url, title)))
        return out

    def collect_candidates(self, topic_dir: Path) -> list[PaperCandidate]:
        raw = Path(topic_dir) / "raw"
        cands = self.candidates_from_sources(raw)
        if not cands:
            cands = self.candidates_from_clean_headers(Path(topic_dir) / "clean")
        if not cands:
            cands = self.candidates_from_clean_headers(raw)
        # high-signal 源置前（cvpr 优先）
        cands.sort(key=lambda p: (0 if p.source_engine == "cvpr" else 1, p.title.lower()))
        return cands

    # ------------------------------------------------------------------ #
    # 匹配
    # ------------------------------------------------------------------ #
    def pick_best(
        self,
        paper_title: str,
        hits: list[SearchHit],
        *,
        min_sim: float | None = None,
        prefer_official: bool | None = None,
    ) -> TalkMatch:
        """在搜索命中里挑最佳 talk；不过闸返回 matched=False。"""
        thr = self.config.min_title_similarity if min_sim is None else min_sim
        prefer = (
            self.config.prefer_official_channel
            if prefer_official is None
            else prefer_official
        )
        best: TalkMatch | None = None
        best_score = -1.0
        for h in hits:
            sim = title_similarity(paper_title, h.title)
            ch = _channel_from_snippet(h.snippet)
            score = sim + (_official_bonus(ch, h.title) if prefer else 0.0)
            # 官方 bonus 只用于排序，过闸仍看纯 sim
            if score > best_score:
                best_score = score
                best = TalkMatch(
                    paper_title=paper_title,
                    video_url=h.url,
                    confidence=sim,
                    matched=sim >= thr,
                    video_title=h.title,
                    channel=ch,
                    reason="ok" if sim >= thr else f"sim {sim:.2f} < {thr:.2f}",
                )
        if best is None:
            return TalkMatch(
                paper_title=paper_title,
                video_url=None,
                confidence=0.0,
                matched=False,
                reason="no hits",
            )
        return best

    async def find_talk(
        self,
        paper_title: str,
        year: int | None = None,
        authors: list[str] | None = None,  # noqa: ARG002 预留
    ) -> TalkMatch:
        conf = self.config.conference or "CVPR"
        parts = [paper_title, conf]
        if year:
            parts.append(str(year))
        query = " ".join(parts)
        try:
            hits = await self.youtube.search(
                query, self.config.search_results_per_paper
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("YouTube 搜 talk 失败 title=%r: %s", paper_title[:60], e)
            return TalkMatch(
                paper_title=paper_title,
                video_url=None,
                confidence=0.0,
                matched=False,
                year=year,
                reason=f"search error: {e}",
            )
        m = self.pick_best(paper_title, hits)
        # dataclass frozen → 用新对象带 year
        return TalkMatch(
            paper_title=m.paper_title,
            video_url=m.video_url,
            confidence=m.confidence,
            matched=m.matched,
            video_title=m.video_title,
            channel=m.channel,
            year=year,
            reason=m.reason,
        )

    # ------------------------------------------------------------------ #
    # 落盘 + enrichment
    # ------------------------------------------------------------------ #
    def write_discovery_note(
        self, raw_dir: Path, match: TalkMatch, *, idx: int
    ) -> Path:
        """写 talk discovery 笔记（不转写），front matter 含 from_paper。"""
        ensure_dir(raw_dir)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", (match.video_title or "talk")[:40]).strip("-")
        fname = f"talk_{idx:02d}_{safe or 'video'}_{stamp}.md"
        fpath = raw_dir / fname
        body = (
            f"<!-- source: {match.video_url} -->\n"
            f"<!-- title: {match.video_title} -->\n"
            f"<!-- from_paper: {match.paper_title} -->\n"
            f"<!-- talk_confidence: {match.confidence:.3f} -->\n"
            f"<!-- fetched: {datetime.now(timezone.utc).isoformat()} -->\n"
            f"<!-- via: talk_linker -->\n\n"
            f"# Talk: {match.video_title or match.paper_title}\n\n"
            f"- paper: {match.paper_title}\n"
            f"- video: {match.video_url}\n"
            f"- channel: {match.channel}\n"
            f"- confidence: {match.confidence:.3f}\n"
            f"- year: {match.year or ''}\n"
            f"- note: talk discovery (not transcribed; set talk.ingest=true for full VideoIngest)\n"
        )
        write_text(fpath, body)
        return fpath

    async def enrich(
        self,
        topic_dir: Path,
        topic: str = "",
        *,
        force: bool = False,
    ) -> TalkEnrichReport:
        """对 topic_dir 做 talk 关联 enrichment。

        - 幂等：raw/.talk_done 存在且 force=False 时跳过
        - 默认只写 discovery；``config.ingest`` 时再调 process_videos
        """
        report = TalkEnrichReport()
        if not self.config.enabled and not force:
            report.warnings.append("talk.enabled=false，跳过")
            return report

        raw_dir = ensure_dir(Path(topic_dir) / "raw")
        done = raw_dir / ".talk_done"
        if done.exists() and not force:
            report.warnings.append("已有 .talk_done，跳过（--no-resume 或删标记可重跑）")
            return report

        cands = self.collect_candidates(topic_dir)
        report.candidates = len(cands)
        if not cands:
            report.warnings.append("无论文候选（sources.json 缺 cvpr/arxiv/thecvf 等）")
            done.write_text("no-candidates\n", encoding="utf-8")
            return report

        limit = self.config.max_talks
        matched_urls: list[str] = []
        idx = 0
        for paper in cands:
            if len(report.matched) >= limit:
                break
            m = await self.find_talk(paper.title, year=paper.year)
            if not m.matched or not m.video_url:
                report.skipped.append(m)
                continue
            idx += 1
            path = self.write_discovery_note(raw_dir, m, idx=idx)
            report.files_written.append(path)
            report.matched.append(m)
            matched_urls.append(m.video_url)
            logger.info(
                "talk 命中 [%.2f] %s → %s",
                m.confidence,
                paper.title[:50],
                m.video_url,
            )

        if self.config.ingest and matched_urls:
            try:
                from .video_pipeline import process_videos

                logger.info(
                    "talk.ingest=true：将转写 %d 个视频（topic=%s）",
                    len(matched_urls),
                    topic or topic_dir.name,
                )
                vr = await process_videos(
                    topic=topic or topic_dir.name,
                    urls=matched_urls,
                    work_dir=Path(topic_dir).parent,
                    run_pipeline=False,
                    use_cache=True,
                )
                report.ingested_urls = list(matched_urls)
                if vr.failed_count:
                    report.warnings.append(
                        f"VideoIngest 部分失败: success={vr.success_count} fail={vr.failed_count}"
                    )
            except Exception as e:  # noqa: BLE001
                report.warnings.append(f"VideoIngest 失败（discovery 笔记已保留）: {e}")
                logger.warning("talk ingest 失败: %s", e)

        summary = {
            "candidates": report.candidates,
            "matched": len(report.matched),
            "skipped": len(report.skipped),
            "files": [str(p.name) for p in report.files_written],
            "ingest": self.config.ingest,
        }
        done.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
