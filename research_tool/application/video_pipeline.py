"""V1.1 VideoIngest 端到端编排器（应用层）。

设计依据：
- [DD-001:M-008/M-012/M-001] 集成入口层
- [PRD-V1.1:FR-006] 下游 5 阶段管道零改动（仅触发 clean→extract→organize→report）
- [PRD-V1.1:F-006] 落 raw/<topic>/video_<id>.md 后 5 阶段自动跑完

职责：
- URL → VideoURL 校验（仅 bilibili + youtube 一期 P0；其他拒绝）
- 并发调度多 URL（M-012 Semaphore(3)）
- 单 URL 全链路：download（yt-dlp）→ transcribe（whisper/groq）→ LLM summary → assemble markdown
- 落盘到 raw/<topic>/video_<id>.md（M-008 MarkdownWriter）
- 触发 5 阶段管道（M-008 PipelineTrigger）—— 可选
- 错误隔离 + 3 段式错误码登记（M-010 复用）

设计模式：协调器 + 适配器（M-008）+ 资源池（M-012）；不修改任何底层模块
约束：仅依赖 M-003 / M-005 / M-007 / M-008 / M-012；通过参数注入便于 mock
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..common.logging_config import emit_log, get_logger
from ..domain.errors import DownloadError, ErrorCode, TranscribeError, register_error
from ..domain.models import (
    LLMSummary,
    VideoMeta,
    VideoURL,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 模块级常量
# --------------------------------------------------------------------------- #

# URL 白名单正则（一期 P0：bilibili + youtube）
# bilibili: bilibili.com / b23.tv 短链
_BILIBILI_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?bilibili\.com/video/(BV[a-zA-Z0-9]+|av\d+|ss\d+|sb\d+).*"
    r"|^(https?://)?b23\.tv/\w+",
    re.IGNORECASE,
)
# youtube: youtube.com / youtu.be
_YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)[\w-]+",
    re.IGNORECASE,
)

DEFAULT_LANGUAGE: str = "zh"
DEFAULT_MAX_URLS: int = 10  # IC-001 上限

# 错误码（与 M-010 体系并行）
E_VID_URL_REJECTED: str = "E_VID_URL_REJECTED"
E_VID_PIPELINE_FAIL: str = "E_VID_PIPELINE_FAIL"

# 视频元信息兜底（VideoIngest 阶段若 downloader 失败时使用）
DEFAULT_VIDEO_META = VideoMeta(
    video_id="unknown",
    platform="unknown",
    title="未命名视频",
    author="未知作者",
    duration_sec=0,
    url="",
    language=DEFAULT_LANGUAGE,
)


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VideoProcessResult:
    """单 URL 处理结果。"""

    url: str
    platform: str = ""
    video_id: str = ""
    status: str = "success"  # "success" | "failed"
    markdown_path: Path | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class VideoPipelineReport:
    """整体编排报告。"""

    topic: str
    results: list[VideoProcessResult] = field(default_factory=list)
    stages_result: Any | None = None  # StagesResult from M-008
    total_duration_ms: int = 0

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


# --------------------------------------------------------------------------- #
# URL 校验
# --------------------------------------------------------------------------- #


def validate_video_url(url: str) -> VideoURL | None:
    """校验 URL，返回 VideoURL 或 None（不支持的平台）。

    Args:
        url: 视频 URL 字符串

    Returns:
        VideoURL(platform, url, video_id) 或 None（拒绝）

    Raises:
        VideoIngestError(E_VID_URL_REJECTED): URL 不在白名单
    """
    from ..domain.errors import VideoIngestError

    if not url or not isinstance(url, str):
        raise VideoIngestError(E_VID_URL_REJECTED, f"URL 为空或非字符串: {url!r}")
    url = url.strip()
    if _BILIBILI_RE.match(url):
        video_id = _extract_bilibili_id(url)
        return VideoURL(platform="bilibili", url=url, video_id=video_id or None)
    if _YOUTUBE_RE.match(url):
        video_id = _extract_youtube_id(url)
        return VideoURL(platform="youtube", url=url, video_id=video_id or None)
    # 拒绝
    register_error(
        ErrorCode.E_VID_002_INVALID_URL.value,
        scene="URL 不在白名单（仅 bilibili / youtube）",
        cause=f"URL {url[:80]} 不匹配 bilibili.com / b23.tv / youtube.com / youtu.be",
        suggestion="使用 B 站或 YouTube 视频完整 URL",
        context={"url": url[:200]},
    )
    raise VideoIngestError(
        E_VID_URL_REJECTED,
        f"URL 不支持（仅 bilibili / youtube 一期 P0）: {url[:60]}",
    )


def _extract_bilibili_id(url: str) -> str:
    m = re.search(r"/(BV[a-zA-Z0-9]+|av\d+|ss\d+|sb\d+)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_youtube_id(url: str) -> str:
    # youtu.be/<id>
    m = re.search(r"youtu\.be/([\w-]+)", url)
    if m:
        return m.group(1)
    # youtube.com/watch?v=<id>
    m = re.search(r"[?&]v=([\w-]+)", url)
    if m:
        return m.group(1)
    # youtube.com/shorts/<id>
    m = re.search(r"shorts/([\w-]+)", url)
    if m:
        return m.group(1)
    return ""


# --------------------------------------------------------------------------- #
# 任务函数（单个 URL 全链路）
# --------------------------------------------------------------------------- #


def build_video_task_func(  # noqa: PLR0915 - many statements OK for orchestrator
    *,
    topic: str,
    work_dir: Path,
    language: str = DEFAULT_LANGUAGE,
    use_cache: bool = True,
    downloader: Any | None = None,
    transcriber_fn: Callable[[str, str], Any] | None = None,
    summarizer_fn: Callable[[str, VideoMeta, str], LLMSummary] | None = None,
    notes_assembler_fn: Callable[[VideoMeta, LLMSummary, list, Any], str] | None = None,
    audio_extractor_fn: Callable[[Path, Path], Awaitable[Path]] | None = None,
    screenshot_count: int = 5,
) -> Callable[[str], Awaitable[Path]]:
    """构造单 URL 任务函数（便于 mock 注入；默认走真实 track-core 模块）。

    Returns:
        异步任务函数：url → markdown 落盘路径
    """
    # 延迟 import（避免循环）
    from ..infrastructure.ingest.notes_schema import assemble_markdown as _assemble

    # 默认 downloader：VideoDownloader
    if downloader is None:
        from ..infrastructure.ingest.downloader import VideoDownloader

        downloader = VideoDownloader(output_dir=work_dir / "videos")

    # 默认 transcriber：transcriber.transcribe
    if transcriber_fn is None:
        from ..infrastructure.ingest.transcriber import transcribe as _transcribe

        def _default_transcribe(audio_path: str, lang: str) -> Any:
            return asyncio.run(
                _transcribe(
                    audio_path,
                    language=lang,
                    read_cache=use_cache,
                    write_cache=use_cache,
                )
            )

        transcriber_fn = _default_transcribe

    # 默认 assembler：notes_schema.assemble_markdown
    if notes_assembler_fn is None:

        def _default_assemble(meta, summary, screenshots, transcript) -> str:
            return _assemble(
                meta=meta,
                summary=summary,
                screenshots=screenshots,
                transcript=transcript,
            )

        notes_assembler_fn = _default_assemble

    async def _task(url: str) -> Path:  # noqa: PLR0915  # WIP: nested pipeline, split pending (P1)
        """单 URL 任务：download → extract_audio → transcribe → summarize → assemble → 落盘。"""
        # 1) URL 校验
        video_url = validate_video_url(url)

        # 2) 下载
        # 使用 duck typing：downloader 有 download(VideoURL) 方法即可
        if hasattr(downloader, "download") and callable(downloader.download):
            dl = downloader
        else:
            from ..infrastructure.ingest.downloader import VideoDownloader

            dl = VideoDownloader(output_dir=work_dir / "videos")

        try:
            download_result = await dl.download(video_url)
        except DownloadError:
            raise

        # 3) ffmpeg 抽音（若 m4a/mp3 已为音频格式则跳过）
        from ..infrastructure.ingest.ffmpeg_wrapper import (
            AudioExtractResult,
            AudioExtractor,
        )

        # 原始下载文件（可能是 mp4 也可能是 m4a）
        original_path = Path(download_result.file_path)
        audio_path = original_path
        # 默认：faster-whisper 支持 m4a/mp3/wav/flac 等；保留 m4a 即可
        if audio_extractor_fn is None:
            # 依据后缀判断：mp4/mkv/webm 是视频容器，需要抽音；m4a/mp3/wav 已是音频
            suffix = audio_path.suffix.lower().lstrip(".")
            if suffix in ("mp4", "mkv", "webm"):
                # 抽音（用 env-aware invoker：FFMPEG_PATH 环境变量优先）
                from ..infrastructure.ingest.ffmpeg_wrapper import (
                    get_ffmpeg_invoker,
                )

                extracted_path = audio_path.with_suffix(".audio.wav")
                try:
                    extractor = AudioExtractor(invoker=get_ffmpeg_invoker())
                    ar: AudioExtractResult = await extractor.extract(audio_path, extracted_path)
                    audio_path = Path(ar.audio_path)
                except Exception:
                    # 抽音失败：退而用原文件（whisper 可能直接吃 mp4）
                    logger.warning("ffmpeg 抽音失败，退用原文件: %s", audio_path)
        else:
            extracted_path = audio_path.with_suffix(".audio.wav")
            audio_path = Path(await audio_extractor_fn(audio_path, extracted_path))

        # 3.5) 关键帧截图（GAP-V3 闭合：从原始视频抽帧，注入 assembler）
        # 仅当原始下载文件是视频容器时尝试；音频文件没法截图。
        screenshots: list = []
        if screenshot_count > 0 and original_path.suffix.lower().lstrip(".") in (
            "mp4",
            "mkv",
            "webm",
            "flv",
            "mov",
        ):
            from ..infrastructure.ingest.ffmpeg_wrapper import (
                KeyframeCapture,
                get_ffmpeg_invoker,
            )
            from ..domain.models import ScreenshotFrame

            try:
                kf = KeyframeCapture(invoker=get_ffmpeg_invoker())
                frames = await kf.capture(
                    original_path,
                    work_dir / "screenshots",
                    count=screenshot_count,
                    video_id=video_url.video_id or download_result.video_id or None,
                )
                # 路径用相对 work_dir 的形式，便于 markdown 跨目录引用
                for fr in frames:
                    rel = fr.path
                    try:
                        rel = fr.path.relative_to(work_dir)
                    except ValueError:
                        rel = fr.path
                    ts = fr.timestamp_sec
                    screenshots.append(
                        ScreenshotFrame(
                            timestamp_sec=ts,
                            path=str(rel),
                            caption=f"@ {int(ts // 60)}:{int(ts % 60):02d}",
                        )
                    )
                logger.info("关键帧截图 %d 张 → %s", len(screenshots), work_dir / "screenshots")
            except Exception as e:  # noqa: BLE001
                logger.warning("关键帧截图失败（继续，不阻断笔记生成）: %s", e)

        # 4) 转写
        try:
            transcript = await asyncio.to_thread(transcriber_fn, str(audio_path), language)
        except TranscribeError:
            raise

        # 5) LLM 总结
        if summarizer_fn is None:
            # 默认 stub（生产环境由 M-006 提供；V1.1 track-integration 不实现 M-006）
            from ..infrastructure.ingest.notes_schema import ChapterDegrader

            degrader = ChapterDegrader()
            title = download_result.title or video_url.video_id or "未知"
            summary = LLMSummary(
                video_summary=f"视频 {title} 的自动总结（默认占位）",
                video_chapters=(
                    degrader.degrade(transcript) if transcript and transcript.segments else []
                ),
                video_takeaways=[
                    "请在测试中注入 summarizer_fn 以启用 LLM 总结",
                ],
                model="default-stub",
            )
        else:
            # summarizer_fn 是同步签名；可能内部用 asyncio.run 调云端 LLM。
            # 放进线程池避免与当前事件循环冲突。
            summary = await asyncio.to_thread(
                summarizer_fn,
                transcript.full_text if transcript else "",
                _make_meta(download_result, video_url),
                transcript.full_text if transcript else "",
            )

        # 6) 拼装 Markdown
        meta = _make_meta(download_result, video_url)
        markdown_text = notes_assembler_fn(
            meta,
            summary,
            screenshots,  # GAP-V3：真实截图列表（可能为空，assembler 会自动跳过）
            transcript,
        )

        # 7) 落盘（M-008 MarkdownWriter）
        from ..infrastructure.ingest.pipeline_adapter import (
            write_markdown,
        )

        out_path = write_markdown(
            md=markdown_text,
            topic=topic,
            video_id=video_url.video_id or download_result.video_id or "unknown",
            work_dir=work_dir,
        )
        return out_path

    return _task


def _make_meta(download_result: Any, video_url: VideoURL) -> VideoMeta:
    """从 download_result 构造 VideoMeta（兼容 dataclass/Pydantic 两种）。"""
    title = getattr(download_result, "title", "") or ""
    duration = getattr(download_result, "duration_sec", 0) or 0
    cover = getattr(download_result, "cover_url", None)
    return VideoMeta(
        video_id=video_url.video_id or getattr(download_result, "video_id", "") or "unknown",
        platform=video_url.platform,
        title=title or "未命名视频",
        author="",
        duration_sec=int(duration),
        url=video_url.url,
        cover_url=cover,
        language=DEFAULT_LANGUAGE,
    )


# --------------------------------------------------------------------------- #
# VideoPipeline 顶层编排
# --------------------------------------------------------------------------- #


class VideoPipeline:
    """V1.1 VideoIngest 端到端编排（应用层）。"""

    def __init__(
        self,
        topic: str,
        work_dir: str | Path = "./research-output",
        *,
        language: str = DEFAULT_LANGUAGE,
        run_pipeline: bool = False,
        max_concurrency: int | None = None,
        use_cache: bool = True,
        summarizer_fn: Callable[[str, VideoMeta, str], LLMSummary] | None = None,
        screenshot_count: int = 5,
    ) -> None:
        self.topic = topic
        self.work_dir = Path(work_dir)
        self.language = language
        self.run_pipeline = run_pipeline
        self.max_concurrency = max_concurrency
        self.use_cache = use_cache
        self.summarizer_fn = summarizer_fn
        self.screenshot_count = screenshot_count
        self._results_so_far: list[VideoProcessResult] = []

    async def process_urls(
        self,
        urls: list[str],
        *,
        task_func: Callable[[str], Awaitable[Path]] | None = None,
    ) -> VideoPipelineReport:
        """处理 URL 列表（并发 + 异常隔离）。

        Args:
            urls: URL 列表（1-10）
            task_func: 自定义任务函数（默认 = build_video_task_func(...)）

        Returns:
            VideoPipelineReport（含每 URL 结果 + 5 阶段管道结果）
            results 保持输入顺序
        """
        start = time.monotonic()

        # 用 url → slot 映射维持输入顺序
        results_by_url: dict[str, VideoProcessResult] = {}

        # 1) URL 校验（拒掉的 URL 立即记为 failed，不进入并发）
        valid: list[str] = []
        for url in urls:
            try:
                validate_video_url(url)
                valid.append(url)
            except Exception as e:  # noqa: BLE001
                results_by_url[url] = VideoProcessResult(
                    url=url,
                    status="failed",
                    error=str(e),
                )

        # 2) 并发处理有效 URL
        if valid:
            from ..infrastructure.ingest.pipeline_adapter import (
                VIDEO_FILENAME_PREFIX,
            )

            if task_func is None:
                task_func = build_video_task_func(
                    topic=self.topic,
                    work_dir=self.work_dir,
                    language=self.language,
                    use_cache=self.use_cache,
                    summarizer_fn=self.summarizer_fn,
                    screenshot_count=self.screenshot_count,
                )

            # 复用 M-012 gather_tasks
            from .video_concurrent_orchestrator import gather_tasks

            orch_results = await gather_tasks(
                valid,
                task_func=task_func,
                concurrency=self.max_concurrency,
            )

            for orch in orch_results:
                if orch.status == "success" and orch.output is not None:
                    out_path = (
                        Path(orch.output) if not isinstance(orch.output, Path) else orch.output
                    )
                    results_by_url[orch.url] = VideoProcessResult(
                        url=orch.url,
                        video_id=out_path.stem.replace(VIDEO_FILENAME_PREFIX, ""),
                        status="success",
                        markdown_path=out_path,
                        duration_ms=orch.duration_ms,
                    )
                else:
                    results_by_url[orch.url] = VideoProcessResult(
                        url=orch.url,
                        status="failed",
                        error=orch.error or "未知错误",
                        duration_ms=orch.duration_ms,
                    )

        # 3) 按输入顺序汇总
        ordered_results = [results_by_url[u] for u in urls if u in results_by_url]

        # 4) 可选：触发 5 阶段管道（clean/extract/organize/report）
        stages_result = None
        if self.run_pipeline and any(r.status == "success" for r in ordered_results):
            from ..infrastructure.ingest.pipeline_adapter import trigger_pipeline

            stages_result = await trigger_pipeline(
                self.topic,
                work_dir=self.work_dir,
                stages=["clean", "extract", "organize", "report"],
            )

        elapsed = int((time.monotonic() - start) * 1000)
        report = VideoPipelineReport(
            topic=self.topic,
            results=ordered_results,
            stages_result=stages_result,
            total_duration_ms=elapsed,
        )
        emit_log(
            "info",
            f"VideoPipeline 完成: {report.success_count} 成功 / {report.failed_count} 失败 "
            f"({elapsed / 1000:.1f}s)",
            step="video_pipeline",
        )
        return report


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


async def process_videos(
    topic: str,
    urls: list[str],
    work_dir: str | Path = "./research-output",
    *,
    language: str = DEFAULT_LANGUAGE,
    run_pipeline: bool = False,
    max_concurrency: int | None = None,
    use_cache: bool = True,
    summarizer_fn: Callable[[str, VideoMeta, str], LLMSummary] | None = None,
    screenshot_count: int = 5,
) -> VideoPipelineReport:
    """模块级便捷函数。"""
    pipeline = VideoPipeline(
        topic=topic,
        work_dir=work_dir,
        language=language,
        run_pipeline=run_pipeline,
        max_concurrency=max_concurrency,
        use_cache=use_cache,
        summarizer_fn=summarizer_fn,
        screenshot_count=screenshot_count,
    )
    return await pipeline.process_urls(urls)


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #

__all__ = [
    # 常量
    "DEFAULT_LANGUAGE",
    "DEFAULT_MAX_URLS",
    "E_VID_URL_REJECTED",
    "E_VID_PIPELINE_FAIL",
    # 数据类
    "VideoProcessResult",
    "VideoPipelineReport",
    # 类
    "VideoPipeline",
    # 函数
    "validate_video_url",
    "build_video_task_func",
    "process_videos",
]
