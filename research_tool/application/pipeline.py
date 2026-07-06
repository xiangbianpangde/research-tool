"""ResearchPipeline —— 编排 5 个 Stage。

依据 01 §7（执行流程）、03 §2（run/stream/StageEvent）、05 §5（幂等与恢复）、
06 §1（仅文件系统通信）、06 §3（Extractor 可选）。
"""

from __future__ import annotations

import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..domain.errors import StageError
from ..infrastructure.llm.base import LLMClient
from ..domain.models import PipelineConfig, PipelineResult, StageEvent
from ..common.slug import slugify
from ..infrastructure.stages.base import ensure_dir, has_output
from ..infrastructure.stages.cleaner import Cleaner
from ..infrastructure.stages.collector import Collector
from ..infrastructure.stages.deepen import DeepenStage
from ..infrastructure.stages.extractor import Extractor
from ..infrastructure.stages.organizer import Organizer
from ..infrastructure.stages.reporter import Reporter


def _stage_output(stage: str, topic_dir: Path) -> tuple[Path, list[str]]:
    return {
        "collect": (topic_dir / "raw", ["*.md"]),
        # deepen 与 collect 同写 raw/，但用独立标记判定完成，避免 resume 误跳（风险 5）
        "deepen": (topic_dir / "raw", [".deepen_done"]),
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
        """流式执行，实时推送每个 Stage 进度。

        P2-6：max_backward_rounds>0 时，完成一次正向后让 organizer 评估知识树，
        把修正查询回到 collect 重跑，最多循环 max_backward_rounds 次。
        """
        topic = topic or self.config.topic
        if not topic:
            raise StageError("pipeline", "缺少 topic")
        topic_dir = ensure_dir(self.config.work_dir / slugify(topic))
        result = PipelineResult(topic_dir=topic_dir)
        self._result = result
        start = time.monotonic()

        for round_n in range(self.config.max_backward_rounds + 1):
            async for ev in self._stream_forward(topic, topic_dir, result):
                yield ev
            if result.failed_stage:
                result.elapsed_sec = time.monotonic() - start
                return
            if round_n >= self.config.max_backward_rounds:
                break
            # 反向传播：评估 → 生成修正查询 → 追加 raw → 失效下游 → 下一轮正向
            if result.organize_result is None:
                break
            yield StageEvent(
                stage="backward",
                status="started",
                message=f"第 {round_n + 1} 轮反向：评估知识树质量",
            )
            try:
                fb = await Organizer(self.config.organizer).assess_and_feedback(
                    result.organize_result,
                    self._get_llm(),
                    topic,
                )
            except Exception as e:  # noqa: BLE001
                yield StageEvent(
                    stage="backward",
                    status="failed",
                    message=f"反向评估失败: {e}",
                )
                break
            if not fb.queries:
                yield StageEvent(
                    stage="backward",
                    status="completed",
                    message="无修正查询，反向终止",
                    data={"sparse_nodes": fb.sparse_nodes},
                )
                break
            yield StageEvent(
                stage="backward",
                status="progress",
                message=f"{len(fb.queries)} 条修正查询，开始重采",
                data={"queries": fb.queries, "sparse_nodes": fb.sparse_nodes},
            )
            await self._recollect(topic, topic_dir, fb.queries)
            self._invalidate_after_collect(topic_dir)
            yield StageEvent(
                stage="backward",
                status="completed",
                message=f"第 {round_n + 1} 轮反向完成，进入下一轮正向",
            )

        result.elapsed_sec = time.monotonic() - start

    async def _stream_forward(
        self,
        topic: str,
        topic_dir: Path,
        result: PipelineResult,
    ) -> AsyncIterator[StageEvent]:
        """一次完整正向：按 self.config.stages 顺序跑 collect→…→report。"""
        for stage in self.config.stages:
            # Extractor 可选（决策 06-3）
            if stage == "extract" and not self.config.extractor.enabled:
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="extractor.enabled=false，跳过",
                )
                continue
            # Deepen 可选（默认启用；deepen.enabled=false 或 --skip deepen 关闭）
            if stage == "deepen" and not self.config.deepen.enabled:
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="deepen.enabled=false，跳过",
                )
                continue
            # PDF 数据源时跳过 deepen（深挖针对 Web 搜索，PDF 摄取无意义）
            if stage == "deepen" and self.config.pdf_dir:
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="PDF 数据源，跳过深挖",
                )
                continue

            out_dir, patterns = _stage_output(stage, topic_dir)
            if self.config.resume and has_output(out_dir, patterns):
                result.stages_skipped.append(stage)
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="已有输出，跳过",
                )
                continue

            yield StageEvent(stage=stage, status="started", message=f"开始 {stage}")
            try:
                await self._exec(stage, topic, topic_dir, result)
            except Exception as e:  # noqa: BLE001 - 记录失败并中断，保留已有输出
                result.failed_stage = stage
                yield StageEvent(stage=stage, status="failed", message=f"{stage} 失败: {e}")
                return  # 外层 stream 见 failed_stage 后会停止反向循环

            result.stages_completed.append(stage)
            yield StageEvent(stage=stage, status="completed", progress=1.0, message=f"{stage} 完成")

    async def _recollect(
        self,
        topic: str,
        topic_dir: Path,
        queries: list[str],
    ) -> None:
        """P2-6 反向：用修正查询追加资料到 raw/。复用 collector 现有去重逻辑。"""
        llm = self._get_llm() if self.config.collector.llm_query_expansion else None
        collector = Collector(self.config.collector, llm)
        sr = await collector.search_queries(queries)
        await collector.fetch_and_store(topic, sr.hits, topic_dir / "raw")

    @staticmethod
    def _invalidate_after_collect(topic_dir: Path) -> None:
        """让 clean/extract/organize/report 在下一轮正向重跑；raw/ 保留全部
        历史 + 新追加。.deepen_done 删除以让 deepen 再消化新资料。"""
        done = topic_dir / "raw" / ".deepen_done"
        if done.exists():
            done.unlink()
        for sub in ("clean", "extracted", "tree"):
            d = topic_dir / sub
            if d.exists():
                shutil.rmtree(d)
        for fname in ("report.md", "report.html"):
            f = topic_dir / fname
            if f.exists():
                f.unlink()

    async def _exec(self, stage: str, topic: str, topic_dir: Path, result: PipelineResult) -> None:
        if stage == "collect":
            if self.config.pdf_dir:
                # PDF 数据源：摄取本地 PDF 文件夹替代 Web 采集
                from ..infrastructure.ingest import PdfIngestor

                pcfg = self.config.pdf_ingest
                llm = self._get_llm() if pcfg.translate else None
                result.collect_result = await PdfIngestor(pcfg, llm).run(
                    self.config.pdf_dir, topic_dir
                )
            else:
                llm = self._get_llm() if self.config.collector.llm_query_expansion else None
                result.collect_result = await Collector(self.config.collector, llm).run(
                    topic, topic_dir
                )
        elif stage == "deepen":
            collector = Collector(self.config.collector, self._get_llm())
            result.deepen_result = await DeepenStage(
                self.config.deepen, collector, self._get_llm()
            ).run(
                topic,
                topic_dir / "raw",
                core_keyword=self.config.collector.core_keyword,
            )
        elif stage == "clean":
            cleaner = Cleaner(self.config.cleaner)
            cr = cleaner.process(topic_dir / "raw", topic_dir)
            if self.config.cleaner.relevance_filter:
                cr = await cleaner.filter_relevance(cr, self._get_llm(), topic)
            result.clean_result = cr
        elif stage == "extract":
            result.extract_result = await Extractor(self.config.extractor).run(
                topic_dir / "clean", self._get_llm(), topic_dir
            )
        elif stage == "organize":
            extracted = topic_dir / "extracted"
            org_input = extracted if (extracted / "entities.json").exists() else topic_dir / "clean"
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
