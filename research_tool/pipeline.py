"""ResearchPipeline —— 编排 5 个 Stage。

依据 01 §7（执行流程）、03 §2（run/stream/StageEvent）、05 §5（幂等与恢复）、
06 §1（仅文件系统通信）、06 §3（Extractor 可选）。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

from .errors import StageError
from .llm.base import LLMClient
from .models import PipelineConfig, PipelineResult, StageEvent
from .slug import slugify
from .stages.base import ensure_dir, has_output
from .stages.cleaner import Cleaner
from .stages.collector import Collector
from .stages.extractor import Extractor
from .stages.organizer import Organizer
from .stages.reporter import Reporter


def _stage_output(stage: str, topic_dir: Path) -> tuple[Path, list[str]]:
    return {
        "collect": (topic_dir / "raw", ["*.md"]),
        "clean": (topic_dir / "clean", ["*.md"]),
        "extract": (topic_dir / "extracted", ["*.json"]),
        "organize": (topic_dir / "tree", ["*.md"]),
        "report": (topic_dir, ["report.md", "report.html"]),
    }[stage]


class ResearchPipeline:
    """调研主管道。"""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._llm: LLMClient | None = None
        self._result: PipelineResult | None = None

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.from_config(self.config.llm)
        return self._llm

    async def run(self, topic: str | None = None) -> PipelineResult:
        """执行全部 Stage（可中断恢复）。"""
        async for _ in self.stream(topic):
            pass
        assert self._result is not None
        return self._result

    async def stream(self, topic: str | None = None) -> AsyncIterator[StageEvent]:
        """流式执行，实时推送每个 Stage 进度。"""
        topic = topic or self.config.topic
        if not topic:
            raise StageError("pipeline", "缺少 topic")
        topic_dir = ensure_dir(self.config.work_dir / slugify(topic))
        result = PipelineResult(topic_dir=topic_dir)
        self._result = result
        start = time.monotonic()

        for stage in self.config.stages:
            # Extractor 可选（决策 06-3）
            if stage == "extract" and not self.config.extractor.enabled:
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage, status="skipped", progress=1.0,
                    message="extractor.enabled=false，跳过",
                )
                continue

            out_dir, patterns = _stage_output(stage, topic_dir)
            if self.config.resume and has_output(out_dir, patterns):
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage, status="skipped", progress=1.0,
                    message="已有输出，跳过",
                )
                continue

            yield StageEvent(stage=stage, status="started", message=f"开始 {stage}")
            try:
                await self._exec(stage, topic, topic_dir, result)
            except Exception as e:  # noqa: BLE001 - 记录失败并中断，保留已有输出
                result.failed_stage = stage
                result.elapsed_sec = time.monotonic() - start
                yield StageEvent(
                    stage=stage, status="failed", message=f"{stage} 失败: {e}"
                )
                return

            result.stages_completed.append(stage)
            yield StageEvent(
                stage=stage, status="completed", progress=1.0, message=f"{stage} 完成"
            )

        result.elapsed_sec = time.monotonic() - start

    async def _exec(
        self, stage: str, topic: str, topic_dir: Path, result: PipelineResult
    ) -> None:
        if stage == "collect":
            llm = self._get_llm() if self.config.collector.llm_query_expansion else None
            result.collect_result = await Collector(self.config.collector, llm).run(
                topic, topic_dir
            )
        elif stage == "clean":
            result.clean_result = Cleaner(self.config.cleaner).process(
                topic_dir / "raw", topic_dir
            )
        elif stage == "extract":
            result.extract_result = await Extractor(self.config.extractor).run(
                topic_dir / "clean", self._get_llm(), topic_dir
            )
        elif stage == "organize":
            extracted = topic_dir / "extracted"
            org_input = (
                extracted if (extracted / "entities.json").exists()
                else topic_dir / "clean"
            )
            result.organize_result = await Organizer(self.config.organizer).run(
                org_input, self._get_llm(), topic_dir, topic=topic
            )
        elif stage == "report":
            result.report_result = await Reporter(self.config.reporter).run(
                topic_dir / "tree", self._get_llm(), topic
            )
        else:
            raise StageError("pipeline", f"未知 stage: {stage}")


def create_pipeline(config: PipelineConfig) -> ResearchPipeline:
    """工厂函数（03 §1 方式3）。"""
    return ResearchPipeline(config)
