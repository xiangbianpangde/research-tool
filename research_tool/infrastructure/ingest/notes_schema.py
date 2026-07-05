"""M-007 笔记结构（V1.1 VideoIngest）。

设计依据：
- [DD-001:M-007 笔记组装器] 6 步 Markdown 拼装（YAML / VideoMeta / 章节降级 /
  截图 / 参考 / Markdown 总装）
- [DD-001:IC-016/017/018/019/020/021] 接口契约
- [DD-001:IC-014] front_matter 字段名 video_ 前缀白名单
- [DD-001:EP-002] 章节降级钩子（V1.2 扩展点）

职责：
- 解析 + 校验 YAML front_matter（video_ 前缀白名单）
- 注入 VideoMeta → front_matter（video_title / video_author / video_duration / video_platform）
- 章节降级：transcript.segments 按 5min 等距切片（无 LLM 时）
- 嵌入截图引用（路径缺失 → 占位图；V1.1 track-core 截图可选，路径可空）
- 生成 ## 参考来源 章节（视频 URL / 平台 / 作者）
- 总装 front_matter + body + 截图 + 参考来源

设计模式：管道-过滤器 + 模板方法；本模块为 M-007 笔记组装的纯数据组装层，
不调 LLM（M-006 调用在 M-008 orchestrator 完成；V1.1 track-core 不做编排）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ...common.logging_config import emit_log, get_logger
from ...domain.models import (
    Chapter,
    LLMSummary,
    ScreenshotFrame,
    Transcript,
    VideoMeta,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 模块级常量
# --------------------------------------------------------------------------- #


DEFAULT_CHAPTER_INTERVAL_MIN: int = 5  # 章节降级默认 5min 等距
FRONT_MATTER_KEY_PREFIX: str = "video_"  # 字段名白名单前缀
PLACEHOLDER_SCREENSHOT_PATH: str = "assets/placeholder.png"  # 截图缺失占位
REFERENCES_SECTION_TITLE: str = "## 参考来源"

# front_matter 必备字段（M-007 契约）
REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "video_title",
    "video_author",
    "video_duration",
    "video_platform",
)

# 错误码
E_LLM_002_CHAPTERS_FALLBACK: str = "E_LLM_002_CHAPTERS_FALLBACK"
E_NS_001_YAML_PARSE_FAIL: str = "E_NS_001_YAML_PARSE_FAIL"
E_NS_002_SCREENSHOT_MISSING: str = "E_NS_002_SCREENSHOT_MISSING"

# front_matter 分隔符
FRONT_MATTER_DELIM: str = "---"


# --------------------------------------------------------------------------- #
# FrontMatterParser（YAML 解析 + video_ 前缀校验）
# --------------------------------------------------------------------------- #


class FrontMatterParser:
    """YAML front_matter 解析器 + video_ 前缀白名单校验。

    异常处理：
    - YAML 解析失败 → 返回空 dict（降级）+ 登记 E_NS_001_YAML_PARSE_FAIL
    - 字段名非法（非 video_ 前缀）→ 加入非法列表（不抛错，便于 M-007 过滤）
    """

    def __init__(self) -> None:
        self._whitelist_prefix = FRONT_MATTER_KEY_PREFIX

    def parse(self, yaml_str: str) -> dict[str, Any]:
        """解析 YAML 字符串。失败时返回空 dict。"""
        if not yaml_str or not yaml_str.strip():
            return {}
        try:
            data = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            from ...domain.errors import ErrorCode, register_error

            register_error(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                scene="YAML front_matter 解析失败",
                cause=str(e),
                suggestion="检查 front_matter 格式（必须为合法 YAML）",
            )
            logger.warning("YAML 解析失败: %s", e)
            return {}
        if data is None:
            return {}
        if not isinstance(data, dict):
            logger.warning("YAML 顶层不是 dict: %s", type(data).__name__)
            return {}
        return data

    def dump(self, front_matter: dict[str, Any]) -> str:
        """序列化 front_matter 为 YAML。"""
        return yaml.safe_dump(
            front_matter,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    def validate_keys(self, front_matter: dict[str, Any]) -> list[str]:
        """返回非 video_ 前缀的非法键名列表。"""
        illegal: list[str] = []
        for k in front_matter.keys():
            if not isinstance(k, str):
                continue
            if not k.startswith(self._whitelist_prefix):
                illegal.append(k)
        return illegal

    def split_md(self, md: str) -> tuple[dict[str, Any], str]:
        """从完整 Markdown 拆出 (front_matter, body)。

        格式约定（V1.1）：
            ---
            video_title: xxx
            ---
            body text...
        """
        if not md.startswith(FRONT_MATTER_DELIM):
            return {}, md
        # 找第二个 ---
        rest = md[len(FRONT_MATTER_DELIM) :]
        m = re.match(r"\s*\n(.*?)\n---\s*\n?(.*)", rest, re.DOTALL)
        if not m:
            return {}, md
        yaml_str = m.group(1)
        body = m.group(2)
        return self.parse(yaml_str), body

    def join_md(self, front_matter: dict[str, Any], body: str) -> str:
        """组装 (front_matter, body) → 完整 Markdown。"""
        yaml_str = self.dump(front_matter)
        return f"{FRONT_MATTER_DELIM}\n{yaml_str}{FRONT_MATTER_DELIM}\n{body}"


# --------------------------------------------------------------------------- #
# VideoMetaInjector（注入 VideoMeta → front_matter）
# --------------------------------------------------------------------------- #


class VideoMetaInjector:
    """将 VideoMeta 注入 front_matter，字段名强制 video_ 前缀。

    字段映射（IC-017）：
        video_id       → video_id
        video_title    → video_title
        author         → video_author
        duration_sec   → video_duration
        platform       → video_platform
        url            → video_url
        cover_url      → video_cover
        language       → video_language
    """

    SCHEMA: ClassVar[dict[str, str]] = {
        "video_id": "video_id",
        "video_title": "video_title",
        "video_author": "video_author",
        "video_duration": "video_duration",
        "video_platform": "video_platform",
        "video_url": "video_url",
        "video_cover": "video_cover",
        "video_language": "video_language",
    }

    def __init__(self) -> None:
        self.schema = dict(self.SCHEMA)

    def inject(self, meta: VideoMeta) -> dict[str, Any]:
        """主注入方法：VideoMeta → front_matter dict。"""
        if not meta:
            return {}
        # 必含 4 字段（IC-017 后置条件）
        out: dict[str, Any] = {
            self.schema["video_id"]: meta.video_id or "",
            self.schema["video_title"]: meta.title or "",
            self.schema["video_author"]: meta.author or "",
            self.schema["video_duration"]: int(meta.duration_sec or 0),
            self.schema["video_platform"]: meta.platform or "",
        }
        if meta.url:
            out[self.schema["video_url"]] = meta.url
        if meta.cover_url:
            out[self.schema["video_cover"]] = meta.cover_url
        if meta.language:
            out[self.schema["video_language"]] = meta.language
        return out

    def fill_defaults(self, meta: VideoMeta) -> VideoMeta:
        """填充缺失默认值（与 inject 配合使用；可选）。"""
        return VideoMeta(
            video_id=meta.video_id or "unknown",
            platform=meta.platform or "unknown",
            title=meta.title or "未命名视频",
            author=meta.author or "未知作者",
            duration_sec=int(meta.duration_sec or 0),
            url=meta.url or "",
            cover_url=meta.cover_url,
            language=meta.language or "zh",
        )


# --------------------------------------------------------------------------- #
# ChapterDegrader（章节降级：5min 等距切片）
# --------------------------------------------------------------------------- #


class ChapterDegrader:
    """章节降级器（无 LLM chapters 时按等距切片）。

    状态机：
        输入有 chapters → 返回原 chapters
        输入无 chapters → 按 interval_min 等距切片生成（来自 transcript.segments）
        transcript.segments 为空 → 返回单个空 chapter
    """

    def __init__(self, interval_min: int = DEFAULT_CHAPTER_INTERVAL_MIN) -> None:
        self.interval_min = max(1, int(interval_min))

    def degrade(
        self,
        transcript: Transcript,
        interval_min: int | None = None,
        existing_chapters: list[Chapter] | None = None,
    ) -> list[Chapter]:
        """主降级方法。

        Args:
            transcript: 转写稿（用于估算总时长）
            interval_min: 等距切片间隔（分钟），None 用 self.interval_min
            existing_chapters: 已有 LLM 章节（如有则直接返回，不降级）

        Returns:
            章节列表（至少 1 个）
        """
        if existing_chapters:
            return list(existing_chapters)
        if not transcript or not transcript.segments:
            return [
                Chapter(
                    start_sec=0.0,
                    end_sec=0.0,
                    title="完整视频",
                    summary="",
                )
            ]
        interval = float(interval_min or self.interval_min)
        interval_sec = interval * 60.0
        # 估算总时长
        total_sec = max((s.end for s in transcript.segments), default=0.0)
        if total_sec <= 0:
            total_sec = float(transcript.segments[-1].end)
        # 切片
        chapters = self._build_chapter_list(transcript, total_sec, interval_sec)
        emit_log(
            "info",
            f"章节降级: {len(chapters)} 段（interval={interval:.0f}min, total={total_sec:.0f}s）",
            step="notes_chapter_degrade",
        )
        return chapters

    def _build_chapter_list(
        self,
        transcript: Transcript,
        total_sec: float,
        interval_sec: float,
    ) -> list[Chapter]:
        """按等距切片构建章节列表。"""
        if total_sec <= 0 or interval_sec <= 0:
            return [
                Chapter(
                    start_sec=0.0,
                    end_sec=max(total_sec, 0.0),
                    title="完整视频",
                    summary="",
                )
            ]
        chapters: list[Chapter] = []
        t = 0.0
        idx = 0
        while t < total_sec:
            end = min(t + interval_sec, total_sec)
            # 从 transcript 找该区段首段文字作为 chapter 标题
            title = self._pick_chapter_title(transcript, t, end, idx)
            chapters.append(
                Chapter(
                    start_sec=round(t, 2),
                    end_sec=round(end, 2),
                    title=title,
                    summary="",
                )
            )
            t = end
            idx += 1
        return chapters

    @staticmethod
    def _pick_chapter_title(
        transcript: Transcript,
        start_sec: float,
        end_sec: float,
        idx: int,
    ) -> str:
        """从区段内首段非空 text 取前 20 字作为标题；否则用默认标题。"""
        for seg in transcript.segments:
            if seg.end < start_sec:
                continue
            if seg.start > end_sec:
                break
            text = (seg.text or "").strip()
            if text:
                # 截前 20 字
                snippet = text[:20] + ("…" if len(text) > 20 else "")
                return f"#{idx + 1} {snippet}"
        return f"#{idx + 1} 章节 {idx + 1}"


# --------------------------------------------------------------------------- #
# ScreenshotEmbedder（截图引用嵌入）
# --------------------------------------------------------------------------- #


class ScreenshotEmbedder:
    """截图引用嵌入器（Markdown 追加 ![](path)）。

    V1.1 track-core 截图可选；path 不存在时 → 占位图。
    """

    def __init__(
        self,
        base_path: str | Path = ".",
        placeholder: str = PLACEHOLDER_SCREENSHOT_PATH,
    ) -> None:
        self.base_path = Path(base_path)
        self.placeholder = placeholder

    def embed(self, md: str, paths: list[str] | list[ScreenshotFrame]) -> str:
        """在 Markdown 末尾追加截图引用。

        Args:
            md: Markdown 文本
            paths: 截图路径列表（支持 str 或 ScreenshotFrame）

        Returns:
            嵌入后的 Markdown
        """
        if not paths:
            return md
        lines: list[str] = [md.rstrip(), "", "## 截图", ""]
        idx = 0
        for p in paths:
            if isinstance(p, ScreenshotFrame):
                ts = p.timestamp_sec
                cap = p.caption or f"@ {ts:.0f}s"
                resolved = self._resolve_path(p.path)
            else:
                resolved = self._resolve_path(p)
                cap = f"截图{idx + 1}"
            lines.append(f"![{cap}]({resolved})")
            idx += 1
        return "\n".join(lines) + "\n"

    def _resolve_path(self, path: str) -> str:
        """路径不存在时返回占位图。"""
        if not path:
            return self.placeholder
        p = Path(path)
        if p.exists():
            return str(p)
        # 在 base_path 下相对路径
        if not p.is_absolute():
            cand = self.base_path / p
            if cand.exists():
                return str(cand)
        # 占位兜底
        emit_log(
            "warning",
            f"截图路径不存在: {path} → 占位图",
            step="notes_screenshot",
        )
        return self.placeholder


# --------------------------------------------------------------------------- #
# ReferenceGenerator（参考来源章节）
# --------------------------------------------------------------------------- #


class ReferenceGenerator:
    """生成 ## 参考来源 章节。"""

    def __init__(self, section_title: str = REFERENCES_SECTION_TITLE) -> None:
        self.section_title = section_title

    def generate(self, meta: VideoMeta) -> str:
        """主生成方法。"""
        return self.format(meta)

    def format(self, meta: VideoMeta) -> str:
        """格式化为 Markdown 章节。"""
        lines: list[str] = [self.section_title, ""]
        if meta.url:
            lines.append(f"- 视频链接: [{meta.platform}]({meta.url})")
        if meta.platform:
            lines.append(f"- 平台: {meta.platform}")
        if meta.author:
            lines.append(f"- 作者/UP主: {meta.author}")
        if meta.video_id:
            lines.append(f"- 视频 ID: `{meta.video_id}`")
        if meta.duration_sec:
            mm, ss = divmod(int(meta.duration_sec), 60)
            hh, mm = divmod(mm, 60)
            dur = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            lines.append(f"- 时长: {dur}")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# MarkdownAssembler（总装 front_matter + body + 截图 + 参考）
# --------------------------------------------------------------------------- #


class MarkdownAssembler:
    """Markdown 总装器（6 步管道的第 6 步）。

    状态机：
        INIT → render front_matter → render body → embed screenshots
              → append references → DONE
    """

    def __init__(self) -> None:
        self.parser = FrontMatterParser()
        self.injector = VideoMetaInjector()
        self.degrader = ChapterDegrader()
        self.embedder = ScreenshotEmbedder()
        self.ref_gen = ReferenceGenerator()

    def assemble(
        self,
        meta: VideoMeta,
        summary: LLMSummary,
        transcript: Transcript,
        screenshots: list[ScreenshotFrame] | None = None,
    ) -> str:
        """拼装完整 Markdown。

        Args:
            meta: 视频元数据
            summary: LLM 总结（video_summary / video_chapters / video_takeaways）
            transcript: 转写稿
            screenshots: 截图列表（可选）

        Returns:
            完整 Markdown（含 YAML front_matter + body + 截图 + 参考来源）
        """
        if not meta or not summary or not transcript:
            return ""

        # 1) front_matter 注入
        front_matter = self.injector.inject(meta)
        # 2) 章节降级（若 LLM 未返回 chapters）
        chapters = self._resolve_chapters(summary, transcript)
        # 3) body
        body = self._render_body(summary, chapters)
        # 4) 截图
        body = self.embedder.embed(body, screenshots or [])
        # 5) 参考来源
        refs = self.ref_gen.generate(meta)
        # 6) 总装
        return self.render(front_matter, body, refs)

    def _resolve_chapters(self, summary: LLMSummary, transcript: Transcript) -> list[Chapter]:
        """有 LLM chapters 用 LLM；无则降级。"""
        if summary.video_chapters:
            return list(summary.video_chapters)
        return self.degrader.degrade(transcript, existing_chapters=None)

    def _render_body(self, summary: LLMSummary, chapters: list[Chapter]) -> str:
        """渲染 Markdown 正文（summary / chapters / takeaways / transcript 摘要）。"""
        lines: list[str] = []
        # Summary
        if summary.video_summary:
            lines.append("## 视频总结")
            lines.append("")
            lines.append(summary.video_summary.strip())
            lines.append("")
        # Chapters
        if chapters:
            lines.append("## 章节")
            lines.append("")
            for ch in chapters:
                ts = self._fmt_timestamp(ch.start_sec)
                lines.append(f"### {ts} {ch.title}")
                if ch.summary:
                    lines.append("")
                    lines.append(ch.summary)
                lines.append("")
        # Takeaways
        if summary.video_takeaways:
            lines.append("## 关键要点")
            lines.append("")
            for t in summary.video_takeaways:
                lines.append(f"- {t}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _fmt_timestamp(sec: float) -> str:
        """秒 → mm:ss 或 hh:mm:ss。"""
        sec = int(sec or 0)
        mm, ss = divmod(sec, 60)
        hh, mm = divmod(mm, 60)
        if hh:
            return f"[{hh:02d}:{mm:02d}:{ss:02d}]"
        return f"[{mm:02d}:{ss:02d}]"

    def render(
        self,
        front_matter: dict[str, Any],
        body: str,
        references: str,
    ) -> str:
        """渲染 front_matter + body + references 为最终 Markdown。"""
        return self.parser.join_md(front_matter, body + "\n" + references)


# --------------------------------------------------------------------------- #
# NotesSchemaOrchestrator（顶层编排）
# --------------------------------------------------------------------------- #


class NotesSchemaOrchestrator:
    """顶层编排器（6 步 Markdown 拼装）。

    依赖注入：5 个子模块都接受覆盖，便于测试 mock。
    """

    def __init__(
        self,
        parser: FrontMatterParser | None = None,
        injector: VideoMetaInjector | None = None,
        degrader: ChapterDegrader | None = None,
        embedder: ScreenshotEmbedder | None = None,
        ref_gen: ReferenceGenerator | None = None,
        assembler: MarkdownAssembler | None = None,
        chapter_interval_min: int = DEFAULT_CHAPTER_INTERVAL_MIN,
    ) -> None:
        self.parser = parser or FrontMatterParser()
        self.injector = injector or VideoMetaInjector()
        self.degrader = degrader or ChapterDegrader(interval_min=chapter_interval_min)
        self.embedder = embedder or ScreenshotEmbedder()
        self.ref_gen = ref_gen or ReferenceGenerator()
        self.assembler = assembler or MarkdownAssembler()
        self.chapter_interval_min = chapter_interval_min

    def assemble_markdown(
        self,
        meta: VideoMeta,
        summary: LLMSummary,
        screenshots: list[ScreenshotFrame],
        transcript: Transcript,
    ) -> str:
        """顶层入口：6 步拼装。"""
        return self.assembler.assemble(
            meta=meta,
            summary=summary,
            transcript=transcript,
            screenshots=screenshots,
        )

    def _build_chapter_fallback(
        self,
        transcript: Transcript,
    ) -> list[Chapter]:
        """EP-002 扩展点：章节降级钩子（V1.2 可重写）。"""
        return self.degrader.degrade(transcript, existing_chapters=None)


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #


def parse_front_matter(yaml_str: str) -> dict[str, Any]:
    """模块级：解析 YAML front_matter。"""
    return FrontMatterParser().parse(yaml_str)


def inject_video_meta(meta: VideoMeta) -> dict[str, Any]:
    """模块级：注入 VideoMeta → front_matter。"""
    return VideoMetaInjector().inject(meta)


def degrade_chapters(
    transcript: Transcript,
    interval_min: int = DEFAULT_CHAPTER_INTERVAL_MIN,
) -> list[Chapter]:
    """模块级：章节降级。"""
    return ChapterDegrader(interval_min=interval_min).degrade(transcript)


def embed_screenshots(md: str, paths: list[str]) -> str:
    """模块级：嵌入截图引用。"""
    return ScreenshotEmbedder().embed(md, paths)


def generate_references(meta: VideoMeta) -> str:
    """模块级：生成参考来源章节。"""
    return ReferenceGenerator().generate(meta)


def assemble_markdown(
    meta: VideoMeta,
    summary: LLMSummary,
    screenshots: list[ScreenshotFrame],
    transcript: Transcript,
) -> str:
    """模块级顶层入口：6 步拼装。"""
    return NotesSchemaOrchestrator().assemble_markdown(meta, summary, screenshots, transcript)


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #


__all__ = [
    # 常量
    "DEFAULT_CHAPTER_INTERVAL_MIN",
    "FRONT_MATTER_KEY_PREFIX",
    "PLACEHOLDER_SCREENSHOT_PATH",
    "REFERENCES_SECTION_TITLE",
    "REQUIRED_FRONTMATTER_FIELDS",
    # 错误码
    "E_LLM_002_CHAPTERS_FALLBACK",
    "E_NS_001_YAML_PARSE_FAIL",
    "E_NS_002_SCREENSHOT_MISSING",
    # 类
    "FrontMatterParser",
    "VideoMetaInjector",
    "ChapterDegrader",
    "ScreenshotEmbedder",
    "ReferenceGenerator",
    "MarkdownAssembler",
    "NotesSchemaOrchestrator",
    # 模块级便捷函数
    "parse_front_matter",
    "inject_video_meta",
    "degrade_chapters",
    "embed_screenshots",
    "generate_references",
    "assemble_markdown",
]
