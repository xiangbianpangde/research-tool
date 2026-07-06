#!/usr/bin/env python
"""V1.1 桥接：Collect 产物 → VideoIngest（GAP-V2 闭合）。

按项目产品哲学（见 memory: project-research-tool-philosophy），主题驱动调研要
"从 Collect 阶段产物里发现视频 URL → 自动喂给 VideoPipeline"，而不是让用户
手填 --video-url。CLI 还没自带这条桥，本脚本是它的可执行替身。

输入：raw/sources.json（Collect 阶段产出）
过滤：
    - source_engine == "bilibili"
    - URL 含 BV 号或 av 号（白名单）
    - 可选 author 过滤（默认要求 author 含"宋浩"）
输出：调用 process_videos() 跑 VideoIngest，落 video_<id>.md 到 raw/，并触发
      下游 clean→extract→organize→report。

用法：
    python scripts/bridge_collect_to_video.py <topic-dir> [--max N] [--author-filter X]
    示例：python scripts/bridge_collect_to_video.py \
            ./research-output/test-songhao/tong-ji-gao-shu-xia-ce-duo-yuan-han-shu-song-hao
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# UTF-8 控制台
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research_tool.application.video_pipeline import process_videos  # noqa: E402
from research_tool.common.logging_config import get_logger, setup_logging  # noqa: E402
from research_tool.domain.models import LLMSummary, VideoMeta  # noqa: E402
from research_tool.infrastructure.llm import LLMClient  # noqa: E402

logger = get_logger(__name__)

_BV_RE = re.compile(r"BV[A-Za-z0-9]{10}")
_AV_RE = re.compile(r"/av(\d+)")


def _topic_from_dir(topic_dir: Path) -> str:
    """从 slug 目录名反推主题（兜底用）。"""
    return topic_dir.name.replace("-", " ")


def _load_sources(topic_dir: Path) -> list[dict]:
    src_json = topic_dir / "raw" / "sources.json"
    if not src_json.exists():
        raise FileNotFoundError(f"找不到 sources.json：{src_json}（先跑 research collect）")
    return json.loads(src_json.read_text(encoding="utf-8"))


_DURATION_RE = re.compile(r"时长 (\d+):(\d+)")


# ----------------------------- Minimax summarizer ---------------------------- #


_SUMMARIZER_SYSTEM = """你是一个数学视频笔记助手。给定一段视频转写文本（中文，可能含 ASR 噪声），
按 JSON schema 输出严谨、结构化的图文笔记骨架：
- video_summary：3-6 句总览，点出本节核心概念 + 与其他章节的联系；
- video_chapters：3-8 个章节，按内容自然分段，每章给 title + summary（2-4 句）+ 起止秒数估算；
- video_takeaways：5-10 条核心要点，每条是可独立成立的知识点陈述；
- model：固定写 "MiniMax-M3"。
要点：
1. 数学公式用 LaTeX inline（$...$）或 block（$$...$$）；
2. 不要复述 ASR 噪声；
3. start_sec/end_sec 按章节占比估算（总时长按转写最后一个时间戳）；
4. 输出必须是合法 JSON，不要加 markdown 包裹。"""


def _build_minimax_summarizer():
    """构造 summarizer_fn：transcript_text/meta/video_text → LLMSummary。"""
    client = LLMClient.create(provider="minimax")

    def _fn(transcript_text: str, meta: VideoMeta, video_text: str) -> LLMSummary:
        # 注：summarizer_fn 是同步签名（video_pipeline 用 to_thread 或直接调）
        # 在异步上下文里同步阻塞 chat_structured 不合适，所以这里用 asyncio.run。
        # 但 build_video_task_func 内部直接同步调 summarizer_fn → 我们用 asyncio.new_event_loop
        text = transcript_text or video_text or ""
        if not text.strip():
            return LLMSummary(
                video_summary="转写为空，无法总结",
                video_chapters=[],
                video_takeaways=[],
                model="MiniMax-M3",
            )
        # 截断超长 transcript（防 token 爆）；保留前 8k char 通常涵盖大致结构
        if len(text) > 8000:
            text = text[:8000] + "\n[... 转写截断 ...]"
        prompt = (
            f"视频标题：{meta.title}\n"
            f"作者：{meta.author}\n"
            f"时长：{meta.duration_sec} 秒\n"
            f"平台：{meta.platform}\n\n"
            f"转写：\n{text}"
        )
        # build_video_task_func 在 task 协程里**同步**调 summarizer_fn（不 await），
        # 所以这里用 asyncio.run 起独立 loop。
        return asyncio.run(client.chat_structured(prompt, LLMSummary, system=_SUMMARIZER_SYSTEM))

    return _fn


# ----------------------------- duration helpers ----------------------------- #


def _duration_min_from_snippet(snippet: str) -> int | None:
    """从 snippet '时长 62:48' 抽分钟数；'时长 6489:18' 也按 mm:ss 解析 → 6489 分钟。"""
    m = _DURATION_RE.search(snippet)
    if not m:
        return None
    return int(m.group(1))


def _filter_video_sources(
    sources: list[dict],
    *,
    author_substr: str | None,
    max_duration_min: int | None,
) -> list[dict]:
    """筛选 bilibili 视频来源；可选按 author 子串过滤（如"宋浩"）。"""
    out: list[dict] = []
    seen_urls: set[str] = set()
    for s in sources:
        if s.get("source_engine") != "bilibili":
            continue
        url = s.get("url") or ""
        if not (_BV_RE.search(url) or _AV_RE.search(url)):
            continue
        if url in seen_urls:
            continue
        # author 过滤：title + snippet 任一含子串即可（V1.1: Source 已扩展 snippet）
        if author_substr:
            haystack = (s.get("title") or "") + " " + (s.get("snippet") or "")
            if author_substr not in haystack:
                continue
        # 时长上限过滤：跳过完整合辑（6000+ 分钟那种）
        if max_duration_min is not None:
            dur = _duration_min_from_snippet(s.get("snippet") or "")
            if dur is not None and dur > max_duration_min:
                logger.info("跳过 %s（时长 %d 分超上限 %d 分）", url[:50], dur, max_duration_min)
                continue
        seen_urls.add(url)
        out.append(s)
    return out


async def _amain(args: argparse.Namespace) -> int:
    topic_dir = Path(args.topic_dir).resolve()
    if not topic_dir.exists():
        logger.error("目录不存在：%s", topic_dir)
        return 2

    topic = args.topic or _topic_from_dir(topic_dir)
    logger.info("桥接启动：topic=%s, topic_dir=%s", topic, topic_dir)

    sources = _load_sources(topic_dir)
    logger.info("载入 %d 个 sources", len(sources))

    video_sources = _filter_video_sources(
        sources,
        author_substr=args.author_filter,
        max_duration_min=args.max_duration_min,
    )
    if args.max > 0:
        video_sources = video_sources[: args.max]

    if not video_sources:
        logger.error(
            "没有匹配的视频 URL（author_filter=%r）。请放宽过滤或先跑更多 collect。",
            args.author_filter,
        )
        return 3

    logger.info("筛得 %d 个视频 URL：", len(video_sources))
    for i, s in enumerate(video_sources):
        logger.info(
            "  %d: %s | %s",
            i + 1,
            (s.get("title") or "")[:60],
            s.get("url"),
        )

    if args.dry_run:
        logger.info("--dry-run：跳过 VideoIngest")
        return 0

    urls = [s["url"] for s in video_sources]
    # topic_dir 已是 slugified 主题目录；VideoIngest 直接落 raw/<slug>/video_*.md
    # work_dir 传 topic_dir.parent → 内部再 slug 一次 = 重复 slug。直接给 topic_dir 当 work_dir。
    summarizer_fn = None
    if args.use_minimax_summary:
        logger.info("启用 Minimax LLM 摘要（provider=minimax, model=MiniMax-M3）")
        summarizer_fn = _build_minimax_summarizer()
    report = await process_videos(
        topic=topic,
        urls=urls,
        work_dir=topic_dir,
        run_pipeline=args.run_pipeline,
        use_cache=not args.no_cache,
        summarizer_fn=summarizer_fn,
        screenshot_count=args.screenshots,
    )
    logger.info(
        "VideoIngest 完成：%d 成功 / %d 失败（%.1fs）",
        report.success_count,
        report.failed_count,
        report.total_duration_ms / 1000,
    )
    for r in report.results:
        if r.status == "success":
            logger.info("  ✓ %s → %s", r.url[:60], r.markdown_path)
        else:
            logger.error("  ✗ %s — %s", r.url[:60], r.error)
    return 0 if report.success_count > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect→VideoIngest 桥接")
    parser.add_argument("topic_dir", help="topic slug 目录，例如 ./research-output/<topic>/<slug>/")
    parser.add_argument("--topic", default=None, help="覆盖主题名（默认从目录名反推）")
    parser.add_argument(
        "--max",
        type=int,
        default=2,
        help="最多处理几个视频（默认 2，避免一次跑爆）",
    )
    parser.add_argument(
        "--author-filter",
        default="宋浩",
        help="UP 主过滤子串；默认'宋浩'。传空串关闭过滤。",
    )
    parser.add_argument(
        "--max-duration-min",
        type=int,
        default=180,
        help="单视频时长上限（分钟）。默认 180=3小时，跳过完整合辑。传 0 关闭。",
    )
    parser.add_argument(
        "--run-pipeline",
        action="store_true",
        help="VideoIngest 完成后自动跑 clean→extract→organize→report",
    )
    parser.add_argument("--no-cache", action="store_true", help="转写不走缓存")
    parser.add_argument(
        "--use-minimax-summary",
        action="store_true",
        help="启用 Minimax LLM 真摘要（替代默认 stub）",
    )
    parser.add_argument(
        "--screenshots",
        type=int,
        default=5,
        help="每个视频抽几张关键帧截图（0=关闭）。默认 5。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出 URL 不跑")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not args.author_filter:
        args.author_filter = None  # 显式置空
    if args.max_duration_min <= 0:
        args.max_duration_min = None
    setup_logging(verbose=args.verbose, quiet=False)
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
