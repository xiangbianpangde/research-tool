"""M-008 管道适配器单元测试。

覆盖：
- MarkdownWriter 落盘（路径生成、权限、磁盘检查）
- PipelineTrigger 九段管道触发与完整性判定
- TagsMerger 合并去重
- CollectConfigInjector 注入
- 模块级便捷函数
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research_tool.common.slug import slugify
from research_tool.domain.errors import VideoIngestError
from research_tool.domain.models import (
    NINE_STAGES,
    CleanerConfig,
    OrganizerConfig,
    PipelineConfig,
    PipelineResult,
    StageEvent,
    LLMSummary,
    Transcript,
    VideoMeta,
)
from research_tool.infrastructure.ingest.notes_schema import assemble_markdown
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.evidence import parse_source
from research_tool.infrastructure.ingest.pipeline_adapter import (
    COLLECT_CONFIG_VERSION,
    E_PIPE_001,
    E_PIPE_CONFIG_MISMATCH,
    E_PIPE_DISK_FULL,
    FILE_PERMS_MARKDOWN,
    MIN_DISK_FREE_BYTES,
    PIPELINE_MAX_RETRY,
    RAW_DIR_NAME,
    VIDEO_FILENAME_PREFIX,
    CollectConfigInjector,
    MarkdownWriter,
    PipelineTrigger,
    StagesResult,
    TagsMerger,
    _sanitize_filename,
    inject_collect_config,
    merge_tags,
    trigger_pipeline,
    write_markdown,
)


# --------------------------------------------------------------------------- #
# MarkdownWriter
# --------------------------------------------------------------------------- #


class TestMarkdownWriter:
    """Markdown 落盘测试。"""

    def test_write_creates_file_in_raw_dir(self, tmp_path: Path):
        writer = MarkdownWriter()
        md = "---\nvideo_id: BV1\n---\n# test\n"
        path = writer.write(md, topic="AI Research", video_id="BV1xx411c7mD", work_dir=tmp_path)
        assert path.exists()
        assert path.parent.name == "raw"
        # 文件名应含 video_ 前缀
        assert path.name.startswith(VIDEO_FILENAME_PREFIX)
        # 内容应一致
        assert path.read_text(encoding="utf-8") == md

    def test_write_creates_topic_slug_dir(self, tmp_path: Path):
        writer = MarkdownWriter()
        path = writer.write(
            "---\nfoo: bar\n---\nbody",
            topic="Transformer 架构",
            video_id="abc",
            work_dir=tmp_path,
        )
        # topic 会被 slugify
        slug_name = path.parent.parent.name
        assert "transformer" in slug_name

    def test_write_creates_nested_dirs(self, tmp_path: Path):
        writer = MarkdownWriter()
        path = writer.write("# test", topic="nested", video_id="v1", work_dir=tmp_path / "deep")
        assert path.exists()
        assert "deep" in str(path)

    def test_write_sanitizes_unsafe_video_id(self, tmp_path: Path):
        writer = MarkdownWriter()
        # 包含非法字符
        path = writer.write("# test", topic="t", video_id='abc<>:"/\\|?*xyz', work_dir=tmp_path)
        assert path.exists()
        # 非法字符应被替换
        assert '<>:"/\\|?*' not in path.name

    def test_write_chmod_0o644(self, tmp_path: Path):
        import os
        import stat

        if os.name == "nt":
            pytest.skip("Windows ACL 模型差异，跳过 0o644 校验")
        writer = MarkdownWriter()
        path = writer.write("# test", topic="t", video_id="v1", work_dir=tmp_path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == FILE_PERMS_MARKDOWN

    def test_check_disk_space_raises_when_low(self, tmp_path: Path, monkeypatch):
        """磁盘剩余 < 100MB 时抛 E_PIPE_DISK_FULL。"""
        writer = MarkdownWriter()
        # 构造极小磁盘使用情况
        fake_usage = MagicMock(free=10 * 1024 * 1024)  # 10MB
        with patch("shutil.disk_usage", return_value=fake_usage):
            with pytest.raises(VideoIngestError) as exc_info:
                writer.write("# x", topic="t", video_id="v1", work_dir=tmp_path)
        assert exc_info.value.code == E_PIPE_DISK_FULL

    def test_write_retry_on_oserror(self, tmp_path: Path, monkeypatch):
        """落盘失败重试 1 次。"""
        writer = MarkdownWriter()
        # 第一次写失败，第二次成功
        original = Path.write_text
        call_count = {"n": 0}

        def flaky_write_text(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("flaky first try")
            return original(self, *args, **kwargs)

        # 同时给 disk_usage 返回充足空间
        fake_usage = MagicMock(free=1024 * 1024 * 1024)  # 1GB
        with (
            patch.object(Path, "write_text", flaky_write_text),
            patch("shutil.disk_usage", return_value=fake_usage),
        ):
            path = writer.write("# x", topic="t", video_id="v1", work_dir=tmp_path)
        assert path.exists()
        assert call_count["n"] == 2  # 重试了 1 次

    def test_write_retry_exhausted_raises(self, tmp_path: Path):
        """连续失败 → E_PIPE_001。"""
        writer = MarkdownWriter()
        fake_usage = MagicMock(free=1024 * 1024 * 1024)

        def always_fail(self, *args, **kwargs):
            raise OSError("persistent failure")

        with (
            patch.object(Path, "write_text", always_fail),
            patch("shutil.disk_usage", return_value=fake_usage),
        ):
            with pytest.raises(VideoIngestError) as exc_info:
                writer.write("# x", topic="t", video_id="v1", work_dir=tmp_path)
        assert exc_info.value.code == E_PIPE_001

    def test_module_level_write_markdown(self, tmp_path: Path):
        path = write_markdown("# hello", topic="t", video_id="v1", work_dir=tmp_path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "# hello"


# --------------------------------------------------------------------------- #
# PipelineTrigger
# --------------------------------------------------------------------------- #


def _assert_stage_artifacts_have_valid_digests(topic_dir: Path) -> None:
    state = json.loads((topic_dir / "runtime-state.json").read_text(encoding="utf-8"))
    assert state["completed_stages"]
    for record in state["completed_stages"].values():
        artifact = topic_dir / record["artifact_path"]
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == record["artifact_digest"]
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        assert envelope["v"] == 1 and envelope["error"] is None


def _assert_final_output_hashes(topic_dir: Path, summary: dict) -> Path:
    output_dir = Path("runs") / summary["run_id"] / "final"
    assert summary["output_dir"] == output_dir.as_posix()
    report_path = topic_dir / summary["output_dir"] / "report.md"
    assert report_path.relative_to(topic_dir).as_posix() in summary["files"]
    for relative, digest in summary["files"].items():
        assert Path(relative).is_relative_to(output_dir)
        assert hashlib.sha256((topic_dir / relative).read_bytes()).hexdigest() == digest
    return report_path


class TestPipelineTrigger:
    """九段管道触发器：实际本地执行与适配契约。"""

    @pytest.mark.asyncio
    async def test_trigger_stages_run(self, tmp_path: Path, monkeypatch):
        """视频 raw/ 进入公开九段流程；旧 stages 参数不能裁剪主链。"""
        topic = "video evidence"
        quote = "The measured battery retained 90 percent capacity after 1000 cycles."
        source_url = "https://www.youtube.com/watch?v=video1"
        markdown = assemble_markdown(
            meta=VideoMeta(
                video_id="video1", platform="youtube", title="Battery test", url=source_url,
            ),
            summary=LLMSummary(
                video_summary=quote + "\n\n" + "Experimental methods and limitations. " * 8,
            ),
            screenshots=[],
            transcript=Transcript(full_text=quote),
        )
        source_path = write_markdown(markdown, topic=topic, video_id="video1", work_dir=tmp_path)
        assert parse_source(source_path.read_text(encoding="utf-8"))["locator"] == source_url
        configurations = []

        def isolated_config(*args, overrides=None, **kwargs):
            cfg = PipelineConfig(
                **overrides,
                cleaner=CleanerConfig(min_content_length=20, relevance_filter=True),
                organizer=OrganizerConfig(min_nodes=1, max_nodes=3, min_evidence_per_node=1),
            )
            configurations.append(cfg)
            return cfg

        def reply(prompt, schema):
            if schema.__name__ == "_ChunkResult":
                assert quote in prompt
                return schema(claims=[{"text": quote, "quote": quote, "topic": "Cycle life"}])
            if schema.__name__ == "_Scores":
                return schema(scores=[1.0])
            if schema.__name__ == "ResearchInspection":
                return schema(findings=[])
            if schema.__name__ == "ReportOutline":
                graph = json.loads(prompt.split("DATA:\n", 1)[1])
                return schema(sections=[{
                    "heading": "Cycle life",
                    "claim_ids": [fact["claim_id"] for fact in graph["facts"]],
                }])
            if schema.__name__ == "ReportSectionDraft":
                facts = json.loads(prompt.split("DATA:\n", 1)[1])
                return schema(paragraphs=[
                    {"text": fact["text"], "claim_ids": [fact["claim_id"]]}
                    for fact in facts
                ])
            if schema.__name__ == "ReportVerification":
                data = json.loads(prompt.split("DATA:\n", 1)[1])
                facts = {fact["claim_id"]: fact for fact in data["facts"]}
                return schema(checks=[{
                    "paragraph_index": index,
                    "supported": paragraph.get("kind") == "heading" and (
                        paragraph["text"] == "Cycle life"
                    ) or all(
                        paragraph["text"] == facts[claim_id]["text"]
                        for claim_id in paragraph["claim_ids"]
                    ),
                    "reason": "Paragraph matches its supplied source quote.",
                } for index, paragraph in enumerate(data["paragraphs"])])
            raise AssertionError(f"unexpected schema: {schema.__name__}")

        llm = MockLLMClient(structured_response=reply)
        monkeypatch.setattr("research_tool.domain.config.load_config", isolated_config)
        monkeypatch.setattr(
            "research_tool.infrastructure.llm.base.LLMClient.from_config", lambda cfg: llm
        )
        trigger = PipelineTrigger(stages=["clean"], max_retry=0)

        result = await trigger.trigger(topic, work_dir=tmp_path)

        assert isinstance(result, StagesResult)
        assert result.success, result.error
        assert result.error is None and result.retried is False
        assert configurations[0].input_dir == source_path.parent
        assert configurations[0].work_dir == tmp_path
        assert configurations[0].stages == list(NINE_STAGES)
        assert configurations[0].resume is True
        topic_dir = tmp_path / slugify(topic)
        summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
        assert summary["verdict"] == "STOP_SUCCESS" and summary["pipeline_complete"] is True
        assert set(summary["stages_completed"]) | set(summary["stages_skipped"]) == set(NINE_STAGES)
        assert "collect" in summary["stages_completed"]
        assert "collect" not in summary["stages_skipped"]
        _assert_stage_artifacts_have_valid_digests(topic_dir)
        assert result.stages_run == summary["stages_completed"]
        report_path = _assert_final_output_hashes(topic_dir, summary)
        report = report_path.read_text(encoding="utf-8")
        assert quote in report and source_url in report
        assert source_path.read_text(encoding="utf-8") == markdown
        assert llm.calls

    @pytest.mark.asyncio
    async def test_trigger_pipeline_failure_returns_failed(self, tmp_path: Path):
        """某阶段失败 → StagesResult(success=False, error=...)"""
        trigger = PipelineTrigger(stages=["clean"])

        async def fake_stream(topic):
            yield StageEvent(stage="clean", status="started", message="")
            yield StageEvent(stage="clean", status="failed", message="clean failed")

        mock_pipeline = MagicMock()
        mock_pipeline.stream = fake_stream
        mock_pipeline._result = PipelineResult(
            topic_dir=tmp_path / "topic", failed_stage="clean", verdict="STOP_ERROR"
        )

        mock_cfg = MagicMock()
        mock_cfg.work_dir = tmp_path

        with (
            patch(
                "research_tool.application.pipeline.ResearchPipeline", return_value=mock_pipeline
            ),
            patch("research_tool.domain.config.load_config", return_value=mock_cfg),
        ):
            result = await trigger.trigger("topic", work_dir=tmp_path)

        assert not result.success
        assert "clean" in (result.error or "")

    @pytest.mark.asyncio
    async def test_trigger_config_load_failure(self, tmp_path: Path):
        """config 加载失败 → StagesResult(success=False, error=config load failed)。"""
        trigger = PipelineTrigger()
        with patch("research_tool.domain.config.load_config", side_effect=ValueError("bad config")):
            result = await trigger.trigger("topic", work_dir=tmp_path)
        assert not result.success
        assert "config load failed" in (result.error or "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("completed", "skipped", "verdict", "success"),
        [
            (list(NINE_STAGES), [], "STOP_SUCCESS", True),
            ([stage for stage in NINE_STAGES if stage not in {"targeted", "merge"}],
             ["targeted", "merge"], "STOP_SUCCESS", True),
            (["clean"], [], "STOP_SUCCESS", False),
            ([], [], "STOP_SUCCESS", False),
            (list(NINE_STAGES), [], "STOP_BUDGET", False),
            (list(NINE_STAGES), [], "CONTINUE", False),
            (list(NINE_STAGES), [], None, False),
        ],
        ids=["complete", "no_gap", "partial", "empty", "budget", "continue", "no_verdict"],
    )
    async def test_trigger_module_level_completion_contract(
        self, tmp_path: Path, completed, skipped, verdict, success
    ):
        """独立适配契约：只有九段完成/明确跳过且闸门成功才返回 success。"""
        async def fake_stream(topic):
            for stage in completed:
                yield StageEvent(stage=stage, status="completed", message="")
            for stage in skipped:
                yield StageEvent(stage=stage, status="skipped", message="")

        mock_pipeline = MagicMock()
        mock_pipeline.stream = fake_stream
        mock_pipeline._result = PipelineResult(
            topic_dir=tmp_path / "topic", stages_completed=completed,
            stages_skipped=skipped, verdict=verdict,
        )

        with (
            patch(
                "research_tool.application.pipeline.ResearchPipeline", return_value=mock_pipeline
            ),
            patch("research_tool.domain.config.load_config", return_value=PipelineConfig()),
        ):
            result = await trigger_pipeline(
                "topic", work_dir=tmp_path, stages=["clean"]
            )

        assert isinstance(result, StagesResult)
        assert result.success is success
        assert result.stages_run == completed
        assert result.retried is (not success)
        assert result.error == (None if success else "failed_stage=incomplete_nine_stage_run")


# --------------------------------------------------------------------------- #
# TagsMerger
# --------------------------------------------------------------------------- #


class TestTagsMerger:
    """tags 合并测试。"""

    def test_merge_basic(self):
        merger = TagsMerger()
        result = merger.merge(["AI", "RAG"], ["rag", "LLM"])
        # "AI" "RAG" "rag" "LLM" → 大小写不敏感去重 → "AI" "RAG" "LLM"
        assert result == ["AI", "RAG", "LLM"]

    def test_merge_preserves_first_occurrence_order(self):
        merger = TagsMerger()
        result = merger.merge(["B", "C"], ["A", "B"])
        # new_tags 在前: B, C + existing: A, B(B dedup) → B, C, A
        assert result == ["B", "C", "A"]

    def test_merge_case_sensitive_mode(self):
        merger = TagsMerger(case_sensitive=True)
        result = merger.merge(["AI", "RAG"], ["ai", "RAG"])
        # new_tags 在前 + case_sensitive=True:
        # AI, RAG, ai, RAG(RAG dedup) → AI, RAG, ai
        assert result == ["AI", "RAG", "ai"]

    def test_merge_dedup_case_insensitive_keeps_first_case(self):
        merger = TagsMerger()
        result = merger.merge(["rag", "LLM"], ["RAG", "llm"])
        # existing: rag, LLM; new: RAG (dedup), llm (dedup) → rag, LLM
        assert result == ["rag", "LLM"]

    def test_merge_empty_inputs(self):
        merger = TagsMerger()
        assert merger.merge([], []) == []
        assert merger.merge(["AI"], []) == ["AI"]
        assert merger.merge([], ["AI"]) == ["AI"]

    def test_merge_filters_empty_strings(self):
        merger = TagsMerger()
        result = merger.merge(["", "  ", "AI"], [""])
        # 过滤空字符串
        assert result == ["AI"]

    def test_module_level_merge_tags(self):
        result = merge_tags(["AI", "RAG"], ["rag", "LLM"])
        assert result == ["AI", "RAG", "LLM"]


# --------------------------------------------------------------------------- #
# CollectConfigInjector
# --------------------------------------------------------------------------- #


class TestCollectConfigInjector:
    """Collect 配置注入测试。"""

    def test_inject_success(self):
        injector = CollectConfigInjector()
        result = injector.inject()
        assert result is True

    def test_inject_returns_bool(self):
        """inject() 必须返回 bool（不抛错）。"""
        injector = CollectConfigInjector()
        result = injector.inject()
        assert isinstance(result, bool)

    def test_inject_with_collect_config_init_failure(self):
        """CollectorConfig() 构造抛错时 → 返回 False（不抛）。"""
        injector = CollectConfigInjector()

        # 直接让 domain.models.CollectorConfig 抛错
        import research_tool.domain.models as models_mod

        original = getattr(models_mod, "CollectorConfig", None)

        # 用一个会抛错的属性
        class Boom:
            def __call__(self, *a, **kw):
                raise RuntimeError("simulated collector failure")

        models_mod.CollectorConfig = Boom()  # type: ignore[assignment]
        try:
            result = injector.inject()
        finally:
            models_mod.CollectorConfig = original  # type: ignore[assignment]
        assert result is False  # 优雅降级返回 False

    def test_module_level_inject_collect_config(self):
        result = inject_collect_config()
        assert isinstance(result, bool)
        # 默认情况下应成功
        assert result is True


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


class TestSanitizeFilename:
    """文件名清洗测试。"""

    def test_basic_safe_input(self):
        assert _sanitize_filename("BV1xx411c7mD") == "BV1xx411c7mD"

    def test_replaces_unsafe_chars(self):
        result = _sanitize_filename("a<b>c:d/e\\f|g?h*i")
        # 所有非法字符应被替换
        for ch in '<>:"/\\|?*':
            assert ch not in result

    def test_replaces_whitespace(self):
        result = _sanitize_filename("hello world")
        assert " " not in result

    def test_strips_leading_trailing_dots(self):
        result = _sanitize_filename("..test..")
        assert not result.startswith(".")
        assert not result.endswith(".")

    def test_empty_input(self):
        assert _sanitize_filename("") == ""
        assert _sanitize_filename(None) == ""  # type: ignore[arg-type]

    def test_truncates_long_input(self):
        long_input = "a" * 200
        result = _sanitize_filename(long_input)
        assert len(result) <= 128


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #


class TestModuleExports:
    """模块级导出与常量。"""

    def test_constants(self):
        assert FILE_PERMS_MARKDOWN == 0o644
        assert MIN_DISK_FREE_BYTES == 100 * 1024 * 1024
        assert PIPELINE_MAX_RETRY == 1
        assert RAW_DIR_NAME == "raw"
        assert VIDEO_FILENAME_PREFIX == "video_"
        assert COLLECT_CONFIG_VERSION == "1.0.0"

    def test_error_codes(self):
        assert E_PIPE_001.startswith("E_PIPE_")
        assert E_PIPE_DISK_FULL.startswith("E_PIPE_")
        assert E_PIPE_CONFIG_MISMATCH.startswith("E_PIPE_")
