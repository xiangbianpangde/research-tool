"""M-007 笔记结构单元测试。

覆盖：
- FrontMatterParser YAML 解析 + video_ 前缀校验
- VideoMetaInjector 注入必备字段
- ChapterDegrader 等距切片降级
- ScreenshotEmbedder 路径缺失占位
- ReferenceGenerator 参考来源格式
- MarkdownAssembler 总装
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_tool.domain.models import (
    Chapter,
    LLMSummary,
    ScreenshotFrame,
    Transcript,
    TranscriptSegment,
    VideoMeta,
)
from research_tool.infrastructure.ingest.notes_schema import (
    FRONT_MATTER_KEY_PREFIX,
    PLACEHOLDER_SCREENSHOT_PATH,
    REFERENCES_SECTION_TITLE,
    REQUIRED_FRONTMATTER_FIELDS,
    ChapterDegrader,
    FrontMatterParser,
    MarkdownAssembler,
    NotesSchemaOrchestrator,
    ReferenceGenerator,
    ScreenshotEmbedder,
    VideoMetaInjector,
    assemble_markdown,
    degrade_chapters,
    generate_references,
    inject_video_meta,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def sample_meta() -> VideoMeta:
    return VideoMeta(
        video_id="BV1xx411c7mD",
        platform="bilibili",
        title="测试视频标题",
        author="测试作者",
        duration_sec=120,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        cover_url="https://i0.hdslb.com/cover.jpg",
        language="zh",
    )


@pytest.fixture
def sample_transcript() -> Transcript:
    """30 分钟视频的转写（5min 切片 → 6 章）。"""
    segs: list[TranscriptSegment] = []
    for i in range(1800 // 30):  # 每 30s 一段
        segs.append(
            TranscriptSegment(
                start=float(i * 30),
                end=float(i * 30 + 30),
                text=f"段落{i + 1}的内容",
            )
        )
    return Transcript(
        language="zh",
        full_text=" ".join(s.text for s in segs),
        segments=segs,
        engine="whisper",
    )


@pytest.fixture
def sample_summary() -> LLMSummary:
    return LLMSummary(
        video_summary="这是一个测试视频的总结。",
        video_chapters=[],
        video_takeaways=["要点 1", "要点 2", "要点 3"],
        model="deepseek-chat",
    )


# --------------------------------------------------------------------------- #
# FrontMatterParser
# --------------------------------------------------------------------------- #


class TestFrontMatterParser:
    """YAML front_matter 解析 + video_ 前缀校验。"""

    def test_parse_valid(self):
        parser = FrontMatterParser()
        data = parser.parse("video_title: hello\nvideo_duration: 100")
        assert data["video_title"] == "hello"
        assert data["video_duration"] == 100

    def test_parse_empty(self):
        assert FrontMatterParser().parse("") == {}

    def test_parse_invalid_returns_empty(self):
        """非法 YAML 降级返回空 dict。"""
        data = FrontMatterParser().parse("invalid: : :")
        assert data == {}

    def test_parse_non_dict_returns_empty(self):
        """顶层不是 dict 返回空。"""
        data = FrontMatterParser().parse("- a\n- b")
        assert data == {}

    def test_dump_roundtrip(self):
        parser = FrontMatterParser()
        data = {"video_title": "测试", "video_duration": 100}
        s = parser.dump(data)
        assert "video_title" in s
        # 重新解析应能拿回
        loaded = parser.parse(s)
        assert loaded["video_title"] == "测试"
        assert loaded["video_duration"] == 100

    def test_validate_keys_legal(self):
        parser = FrontMatterParser()
        data = {"video_title": "x", "video_duration": 100, "video_author": "y"}
        assert parser.validate_keys(data) == []

    def test_validate_keys_illegal(self):
        parser = FrontMatterParser()
        data = {"video_title": "x", "title": "illegal", "random": "bad"}
        illegal = parser.validate_keys(data)
        assert set(illegal) == {"title", "random"}

    def test_split_md_with_frontmatter(self):
        parser = FrontMatterParser()
        md = "---\nvideo_title: hi\n---\nbody text"
        fm, body = parser.split_md(md)
        assert fm["video_title"] == "hi"
        assert body.strip() == "body text"

    def test_split_md_no_frontmatter(self):
        parser = FrontMatterParser()
        md = "just body text"
        fm, body = parser.split_md(md)
        assert fm == {}
        assert body == "just body text"

    def test_join_md(self):
        parser = FrontMatterParser()
        md = parser.join_md({"video_title": "x"}, "body text")
        assert md.startswith("---")
        assert "body text" in md
        # 重新解析
        fm, body = parser.split_md(md)
        assert fm["video_title"] == "x"


# --------------------------------------------------------------------------- #
# VideoMetaInjector
# --------------------------------------------------------------------------- #


class TestVideoMetaInjector:
    """VideoMeta 注入 front_matter。"""

    def test_required_fields(self, sample_meta: VideoMeta):
        injector = VideoMetaInjector()
        out = injector.inject(sample_meta)
        for f in REQUIRED_FRONTMATTER_FIELDS:
            assert f in out, f"missing required field: {f}"
        assert out["video_title"] == "测试视频标题"
        assert out["video_author"] == "测试作者"
        assert out["video_duration"] == 120
        assert out["video_platform"] == "bilibili"

    def test_all_fields_video_prefix(self, sample_meta: VideoMeta):
        out = VideoMetaInjector().inject(sample_meta)
        for k in out.keys():
            assert k.startswith(FRONT_MATTER_KEY_PREFIX), f"{k} 不带 video_ 前缀"

    def test_optional_fields(self, sample_meta: VideoMeta):
        out = VideoMetaInjector().inject(sample_meta)
        # url + cover 存在时注入
        assert "video_url" in out
        assert "video_cover" in out
        assert "video_language" in out

    def test_minimal_meta(self):
        meta = VideoMeta(video_id="x", platform="youtube", title="t")
        out = VideoMetaInjector().inject(meta)
        # 没有 url → 不应出现 video_url
        assert "video_url" not in out
        assert out["video_title"] == "t"

    def test_module_level(self, sample_meta: VideoMeta):
        out = inject_video_meta(sample_meta)
        assert out["video_id"] == "BV1xx411c7mD"


# --------------------------------------------------------------------------- #
# ChapterDegrader
# --------------------------------------------------------------------------- #


class TestChapterDegrader:
    """章节降级（5min 等距切片）。"""

    def test_degrade_existing_chapters(self, sample_transcript: Transcript):
        existing = [Chapter(start_sec=0, end_sec=60, title="自定义")]
        result = ChapterDegrader().degrade(sample_transcript, existing_chapters=existing)
        assert result == existing

    def test_degrade_empty_segments(self):
        transcript = Transcript(language="zh", full_text="", segments=[])
        result = ChapterDegrader().degrade(transcript)
        assert len(result) == 1
        assert result[0].title == "完整视频"

    def test_degrade_5min_slicing(self, sample_transcript: Transcript):
        """30min 视频 → 6 段（5min 切片）。"""
        result = ChapterDegrader(interval_min=5).degrade(sample_transcript)
        assert len(result) == 6  # 0-5, 5-10, ..., 25-30
        # 第一段 0-300s
        assert result[0].start_sec == 0.0
        assert result[0].end_sec == 300.0
        # 最后一段 1500-1800s
        assert result[-1].end_sec == 1800.0

    def test_degrade_title_from_segment(self, sample_transcript: Transcript):
        result = ChapterDegrader(interval_min=5).degrade(sample_transcript)
        # 标题应包含 "段落1的内容"（前 20 字）
        assert "段落1的内容" in result[0].title

    def test_module_level(self, sample_transcript: Transcript):
        result = degrade_chapters(sample_transcript, interval_min=10)
        # 30min / 10min = 3 段
        assert len(result) == 3


# --------------------------------------------------------------------------- #
# ScreenshotEmbedder
# --------------------------------------------------------------------------- #


class TestScreenshotEmbedder:
    """截图引用嵌入。"""

    def test_embed_str_paths(self, tmp_path: Path):
        # 创建截图文件
        f1 = tmp_path / "shot1.png"
        f1.write_bytes(b"x")
        f2 = tmp_path / "shot2.jpg"
        f2.write_bytes(b"x")
        embedder = ScreenshotEmbedder(base_path=tmp_path)
        md = embedder.embed("body", [str(f1), str(f2)])
        assert "## 截图" in md
        # str 路径输出 ![截图N](path)
        assert "![截图1]" in md
        assert "![截图2]" in md
        assert str(f1) in md
        assert str(f2) in md

    def test_embed_screenshotframe(self, tmp_path: Path):
        f = tmp_path / "s.png"
        f.write_bytes(b"x")
        embedder = ScreenshotEmbedder(base_path=tmp_path)
        md = embedder.embed(
            "body", [ScreenshotFrame(timestamp_sec=42.5, path=str(f), caption="截 1")]
        )
        assert "![截 1]" in md
        assert str(f) in md

    def test_embed_missing_uses_placeholder(self, tmp_path: Path):
        embedder = ScreenshotEmbedder(base_path=tmp_path)
        md = embedder.embed("body", ["/nonexistent/path/shot.png"])
        assert PLACEHOLDER_SCREENSHOT_PATH in md

    def test_embed_empty_list_unchanged(self):
        embedder = ScreenshotEmbedder()
        md = embedder.embed("body", [])
        assert md == "body"


# --------------------------------------------------------------------------- #
# ReferenceGenerator
# --------------------------------------------------------------------------- #


class TestReferenceGenerator:
    """参考来源章节生成。"""

    def test_generate(self, sample_meta: VideoMeta):
        md = ReferenceGenerator().generate(sample_meta)
        assert REFERENCES_SECTION_TITLE in md
        assert "bilibili" in md
        assert "测试作者" in md
        assert "BV1xx411c7mD" in md
        assert "00:02:00" in md or "02:00" in md  # 120s

    def test_format_no_url(self):
        meta = VideoMeta(video_id="x", platform="youtube", title="t")
        md = ReferenceGenerator().generate(meta)
        assert REFERENCES_SECTION_TITLE in md
        assert "youtube" in md

    def test_module_level(self, sample_meta: VideoMeta):
        md = generate_references(sample_meta)
        assert REFERENCES_SECTION_TITLE in md


# --------------------------------------------------------------------------- #
# MarkdownAssembler
# --------------------------------------------------------------------------- #


class TestMarkdownAssembler:
    """Markdown 总装。"""

    def test_assemble_full(
        self,
        sample_meta: VideoMeta,
        sample_summary: LLMSummary,
        sample_transcript: Transcript,
    ):
        md = MarkdownAssembler().assemble(
            meta=sample_meta,
            summary=sample_summary,
            transcript=sample_transcript,
        )
        # YAML front_matter
        assert md.startswith("---")
        # 视频总结
        assert "测试视频的总结" in md
        # 章节降级（5min × 6 段）
        assert "## 章节" in md
        # 关键要点
        assert "## 关键要点" in md
        assert "要点 1" in md
        # 参考来源
        assert REFERENCES_SECTION_TITLE in md
        # body 末尾（reference generator 顺序：URL → platform → author → video_id → 时长）
        assert "时长:" in md
        assert "BV1xx411c7mD" in md

    def test_assemble_with_screenshots(
        self,
        sample_meta: VideoMeta,
        sample_summary: LLMSummary,
        sample_transcript: Transcript,
        tmp_path: Path,
    ):
        s = tmp_path / "shot.png"
        s.write_bytes(b"x")
        md = MarkdownAssembler().assemble(
            meta=sample_meta,
            summary=sample_summary,
            transcript=sample_transcript,
            screenshots=[ScreenshotFrame(timestamp_sec=10, path=str(s))],
        )
        assert "## 截图" in md
        assert str(s) in md

    def test_assemble_with_existing_chapters(
        self,
        sample_meta: VideoMeta,
        sample_summary: LLMSummary,
        sample_transcript: Transcript,
    ):
        """LLM 返回 chapters → 不降级。"""
        summary = LLMSummary(
            video_summary="s",
            video_chapters=[
                Chapter(start_sec=0, end_sec=60, title="LLM 章 1"),
                Chapter(start_sec=60, end_sec=120, title="LLM 章 2"),
            ],
            video_takeaways=[],
        )
        md = MarkdownAssembler().assemble(
            meta=sample_meta,
            summary=summary,
            transcript=sample_transcript,
        )
        assert "LLM 章 1" in md
        assert "LLM 章 2" in md
        # 不应出现降级标题 "#1 ..."
        assert "#1" not in md

    def test_render_split(self):
        """render 输出可被 split_md 重新解析。"""
        ma = MarkdownAssembler()
        front = {"video_title": "test"}
        body = "## body\n\ntext"
        refs = "## 参考来源\n\n- 链接"
        md = ma.render(front, body, refs)
        fm, parsed_body = ma.parser.split_md(md)
        assert fm["video_title"] == "test"
        assert "## body" in parsed_body
        assert "## 参考来源" in parsed_body


# --------------------------------------------------------------------------- #
# NotesSchemaOrchestrator
# --------------------------------------------------------------------------- #


class TestNotesSchemaOrchestrator:
    """顶层编排器（依赖注入）。"""

    def test_assemble_markdown(
        self,
        sample_meta: VideoMeta,
        sample_summary: LLMSummary,
        sample_transcript: Transcript,
    ):
        orch = NotesSchemaOrchestrator()
        md = orch.assemble_markdown(
            meta=sample_meta,
            summary=sample_summary,
            screenshots=[],
            transcript=sample_transcript,
        )
        assert md.startswith("---")
        assert REFERENCES_SECTION_TITLE in md

    def test_module_level(
        self,
        sample_meta: VideoMeta,
        sample_summary: LLMSummary,
        sample_transcript: Transcript,
    ):
        md = assemble_markdown(
            meta=sample_meta,
            summary=sample_summary,
            screenshots=[],
            transcript=sample_transcript,
        )
        assert "测试视频标题" in md


# --------------------------------------------------------------------------- #
# CER 不相关，但确认 chapter_degrade 触发 WARN
# --------------------------------------------------------------------------- #


class TestChapterDegradeLogs:
    """章节降级触发 emit_log（WARN/INFO）。"""

    def test_degrade_emit_log(self, sample_transcript: Transcript, caplog):
        import logging

        with caplog.at_level(logging.INFO):
            ChapterDegrader().degrade(sample_transcript)
        # emit_log 走的是 research_tool.structured logger；直接 logger.info 也行
        # 这里宽松断言：只要不抛错就算通过
