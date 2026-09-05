"""ResearchPipeline —— 编排 5 个 Stage。

依据 01 §7（执行流程）、03 §2（run/stream/StageEvent）、05 §5（幂等与恢复）、
06 §1（仅文件系统通信）、06 §3（Extractor 可选）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..domain.errors import LLMAuthenticationError, StageError
from ..infrastructure.llm.base import LLMClient
from ..domain.models import PipelineConfig, PipelineResult, StageEvent, StageRunMetric
from ..common.slug import slugify
from ..infrastructure.stages.base import ensure_dir, has_output, read_json, write_json
from ..infrastructure.stages.cleaner import Cleaner
from ..infrastructure.stages.collector import Collector
from ..infrastructure.stages.deepen import DeepenStage
from ..infrastructure.stages.extractor import Extractor
from ..infrastructure.stages.organizer import Organizer
from ..infrastructure.stages.reporter import Reporter

logger = logging.getLogger(__name__)


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
        self._validate_stage_dag(config.stages, mode=config.mode)
        self._llm: LLMClient | None = None
        self._result: PipelineResult | None = None
        # B8 产品集成装配点：kill-switch=false → 逐字节 legacy（忽略一切子 flag）。
        # 仅在 nine_loop.* 显式开启时解析 flag 快照；默认全关时为空 dict，
        # 任何阶段都不读取/不派发 nine 分支（零用户可见变更）。
        self._nine_loop_flags: dict = {}
        self._nine_loop_active = False
        if config.nine_loop.enabled:
            from ..nine_loop import flags as _flags_mod
            from ..nine_loop.flags import load, resolve, verify_flags
            _vs = verify_flags()

            cfg = {
                "nine_loop.enabled": config.nine_loop.enabled,
                "nine_loop.gate.enabled": config.nine_loop.gate_enabled,
                "nine_loop.deepen_as_strategy": config.nine_loop.deepen_as_strategy,
                "rt_identity.adapter.enabled": config.nine_loop.adapter_enabled,
                "nine_loop.shadow.enabled": config.nine_loop.shadow_enabled,
                "nine_loop.shadow.sample_rate": config.nine_loop.shadow_sample_rate,
            }
            for _s, _v in config.nine_loop.stages.items():
                cfg[f"nine_loop.stages.{_s}"] = _v
            resolved = resolve(load(cfg))
            if resolved.get("nine_loop_enabled"):
                self._nine_loop_active = True
                self._nine_loop_flags = resolved

    def nine_loop_flags(self) -> dict:
        """当前装配的九段 flag 快照（默认全关时返回空 dict）。只读。"""
        return dict(self._nine_loop_flags)

    def shadow_root(self) -> Path:
        """shadow sidecar 根（产品 data dir 旁 shadow/<run_id>/）。

        仅 shadow_enabled 时有效；kill-switch=false 时本方法返回 work_dir 旁
        的 shadow/ 路径但不创建任何目录，调用方必须先在 flags 里确认
        shadow.enabled 再写。shadow 产物永不写回 legacy 输出根。
        """
        return self.config.work_dir.parent / "shadow" / self.config.work_dir.name


    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.from_config(self.config.llm)
        return self._llm

    @staticmethod
    def _validate_stage_dag(stages: list[str], mode: str = "full") -> None:
        """拓扑 DAG 依赖校验：严格断言相对顺序与必要的前置依赖，禁止倒置或断层依赖。"""
        order = {s: i for i, s in enumerate(stages)}
        # 1) 倒置依赖硬校验：任何前置阶段不得出现在后置阶段之后（P1/P2-F 闭环）
        if "collect" in order and "clean" in order and order["collect"] > order["clean"]:
            raise StageError("pipeline", "阶段拓扑错误：collect 必须在 clean 之前执行")
        if "clean" in order and "extract" in order and order["clean"] > order["extract"]:
            raise StageError("pipeline", "阶段拓扑错误：clean 必须在 extract 之前执行")
        if "clean" in order and "organize" in order and order["clean"] > order["organize"]:
            raise StageError("pipeline", "阶段拓扑错误：clean 必须在 organize 之前执行")
        if "extract" in order and "organize" in order and order["extract"] > order["organize"]:
            raise StageError("pipeline", "阶段拓扑错误：extract 必须在 organize 之前执行")
        if "organize" in order and "report" in order and order["organize"] > order["report"]:
            raise StageError("pipeline", "阶段拓扑错误：organize 必须在 report 之前执行")

        # 2) 多阶段链路断层缺失校验（P1/P2-F 闭环）：仅在多阶段管线时检查，保留单阶段独立调试自由度
        if len(stages) > 1:
            if "collect" in order and "extract" in order and "clean" not in order:
                raise StageError("pipeline", "阶段拓扑依赖缺失：从 collect 到 extract 必须经过 clean 阶段清洗")
            if "clean" in order and "report" in order and "organize" not in order and mode != "brief":
                raise StageError("pipeline", "阶段拓扑依赖缺失：从 clean 到 report 必须经过 organize 构建知识树")
            if "extract" in order and "report" in order and "organize" not in order and mode != "brief":
                raise StageError("pipeline", "阶段拓扑依赖缺失：从 extract 到 report 必须经过 organize 构建知识树")
            if "collect" in order and "report" in order and "clean" not in order:
                raise StageError("pipeline", "阶段拓扑依赖缺失：从 collect 到 report 必须经过 clean 阶段清洗")

    def _clean_input_fingerprint(self, topic_dir: Path) -> str:
        """生成 Clean 阶段完整输入+契约的 SHA-256 签名（P0-C 终极闭环）。
        涵盖全部 cleaner 配置字段以及 raw/*.md 的真实文件内容字节哈希。
        """
        cfg_data = {
            "relevance_filter": bool(self.config.cleaner.relevance_filter),
            "relevance_threshold": float(self.config.cleaner.relevance_threshold),
            "dedup_similarity": float(self.config.cleaner.dedup_similarity),
            "max_content_length": int(self.config.cleaner.max_content_length),
            "strip_html": bool(self.config.cleaner.strip_html),
            "strip_ads": bool(self.config.cleaner.strip_ads),
        }
        raw_dir = topic_dir / "raw"
        raw_hashes: list[str] = []
        if raw_dir.exists():
            for p in sorted(raw_dir.glob("*.md")):
                try:
                    content_hash = hashlib.sha256(p.read_bytes()).hexdigest()
                    raw_hashes.append(f"{p.name}:{content_hash}")
                except OSError:
                    pass
        blob = json.dumps({"config": cfg_data, "raw_content": raw_hashes}, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _stage_uses_llm(self, stage: str) -> bool:
        if stage == "collect":
            return bool(
                self.config.collector.llm_query_expansion
                or (self.config.pdf_dir and self.config.pdf_ingest.translate)
            )
        if stage == "deepen":
            return True
        if stage == "clean":
            return self.config.cleaner.relevance_filter
        return stage in {"extract", "organize", "report"}

    @staticmethod
    def _completion_marker(topic_dir: Path, stage: str) -> Path:
        return topic_dir / ".stage-complete" / f"{stage}.json"

    def _stage_is_complete(
        self,
        stage: str,
        topic_dir: Path,
        out_dir: Path,
        patterns: list[str],
    ) -> bool:
        marker = self._completion_marker(topic_dir, stage)
        if marker.is_file():
            # 契约绑定校验（P0-C 终极闭环）：Clean 阶段严格校验完整输入内容哈希与配置签名
            if stage == "clean":
                try:
                    data = read_json(marker) or {}
                    saved_fp = data.get("clean_input_fingerprint")
                    if not saved_fp or saved_fp != self._clean_input_fingerprint(topic_dir):
                        return False
                except Exception:
                    return False
            return True
        # 核心 LLM 阶段（含开启 relevance_filter 的 clean）必须有成功 marker，
        # 避免把超时/崩溃/鉴权失败前留下的部分文件误判成完整阶段（P0 状态机防护）。
        if stage in {"extract", "organize", "report"} or (
            stage == "clean" and self.config.cleaner.relevance_filter
        ):
            return False
        return has_output(out_dir, patterns)

    def _mark_stage_complete(self, topic_dir: Path, stage: str) -> None:
        marker = self._completion_marker(topic_dir, stage)
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        payload: dict[str, Any] = {
            "stage": stage,
            "status": "completed",
            "timestamp": time.time(),
        }
        if stage == "clean":
            payload["clean_input_fingerprint"] = self._clean_input_fingerprint(topic_dir)
        write_json(temporary, payload)
        temporary.replace(marker)

    async def _exec_with_retry(
        self,
        stage: str,
        topic: str,
        topic_dir: Path,
        result: PipelineResult,
    ) -> None:
        uses_llm = self._stage_uses_llm(stage)
        attempts = self.config.llm_stage_attempts if uses_llm else 1
        for attempt in range(1, attempts + 1):
            try:
                if uses_llm:
                    await self._get_llm().healthcheck(
                        timeout_sec=self.config.llm_healthcheck_timeout_sec
                    )
                await self._exec(stage, topic, topic_dir, result)
                return
            except LLMAuthenticationError:
                raise
            except Exception:  # noqa: BLE001 - 最后一次由外层统一记录 StageError
                if attempt >= attempts:
                    raise
                delay = self.config.llm_retry_backoff_sec * attempt
                logger.warning(
                    "%s 第 %d/%d 次执行失败，%.1fs 后重试",
                    stage,
                    attempt,
                    attempts,
                    delay,
                )
                if delay:
                    await asyncio.sleep(delay)

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
                self._finish_run(result, start)
                return
            if round_n >= self.config.max_backward_rounds:
                break
            # 反向传播：评估 → 生成修正查询 → 追加 raw → 失效下游 → 下一轮正向
            if result.organize_result is None:
                break
            backward_start = time.monotonic()
            yield StageEvent(
                stage="backward",
                status="started",
                message=f"第 {round_n + 1} 轮反向：评估知识树质量",
            )
            try:
                await self._get_llm().healthcheck(
                    timeout_sec=self.config.llm_healthcheck_timeout_sec
                )
                fb = await Organizer(self.config.organizer).assess_and_feedback(
                    result.organize_result,
                    self._get_llm(),
                    topic,
                )
            except LLMAuthenticationError as e:
                duration = max(0.0, time.monotonic() - backward_start)
                result.failed_stage = "backward"
                message = f"反向评估失败: {e}"
                result.stage_metrics.append(
                    StageRunMetric(
                        stage="backward",
                        status="failed",
                        duration_sec=duration,
                        message=message,
                    )
                )
                yield StageEvent(
                    stage="backward",
                    status="failed",
                    message=message,
                    data={"duration_sec": duration},
                )
                self._finish_run(result, start)
                return
            except Exception as e:  # noqa: BLE001
                duration = max(0.0, time.monotonic() - backward_start)
                message = f"反向评估失败: {e}"
                result.stage_metrics.append(
                    StageRunMetric(
                        stage="backward",
                        status="failed",
                        duration_sec=duration,
                        message=message,
                    )
                )
                yield StageEvent(
                    stage="backward",
                    status="failed",
                    message=message,
                    data={"duration_sec": duration},
                )
                break
            if not fb.queries:
                duration = max(0.0, time.monotonic() - backward_start)
                result.stage_metrics.append(
                    StageRunMetric(
                        stage="backward",
                        status="completed",
                        duration_sec=duration,
                        message="无修正查询，反向终止",
                    )
                )
                yield StageEvent(
                    stage="backward",
                    status="completed",
                    message="无修正查询，反向终止",
                    data={"sparse_nodes": fb.sparse_nodes, "duration_sec": duration},
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
            duration = max(0.0, time.monotonic() - backward_start)
            result.stage_metrics.append(
                StageRunMetric(stage="backward", status="completed", duration_sec=duration)
            )
            yield StageEvent(
                stage="backward",
                status="completed",
                message=f"第 {round_n + 1} 轮反向完成，进入下一轮正向",
                data={"duration_sec": duration},
            )

        self._finish_run(result, start)

    def _finish_run(self, result: PipelineResult, started_at: float) -> None:
        result.elapsed_sec = max(0.0, time.monotonic() - started_at)
        summary_path = result.topic_dir / "run-summary.json"
        audit_path = result.topic_dir / "raw" / "source-audit.json"
        if audit_path.exists():
            try:
                source_audits = list((read_json(audit_path) or {}).get("sources") or [])
            except (OSError, ValueError, TypeError):
                source_audits = []
        else:
            source_audits = (
                [audit.model_dump() for audit in result.collect_result.source_audits]
                if result.collect_result
                else []
            )
        accounted_stages = set(result.stages_completed) | set(result.stages_skipped)
        pipeline_complete = result.failed_stage is None and set(self.config.stages).issubset(
            accounted_stages
        )
        write_json(
            summary_path,
            {
                "version": 1,
                "mode": self.config.mode,
                "output_contract": (
                    self.config.mode if self.config.mode in {"brief", "full"} else "legacy"
                ),
                "pipeline_complete": pipeline_complete,
                "elapsed_sec": round(result.elapsed_sec, 3),
                "resume_enabled": self.config.resume,
                "llm_stage_attempts": self.config.llm_stage_attempts,
                "search_cache_enabled": self.config.collector.search_cache,
                "stages_completed": list(result.stages_completed),
                "stages_skipped": list(result.stages_skipped),
                "failed_stage": result.failed_stage,
                "stage_metrics": [metric.model_dump() for metric in result.stage_metrics],
                "source_audits": source_audits,
            },
        )
        result.run_summary_path = summary_path

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
                result.stage_metrics.append(
                    StageRunMetric(
                        stage=stage,
                        status="skipped",
                        message="extractor.enabled=false，跳过",
                    )
                )
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="extractor.enabled=false，跳过",
                )
                continue
            # 九段策略降级：deepen 降级为收集/补搜策略，不作为独立阶段执行
            if stage == "deepen" and (
                self._nine_loop_flags.get("deepen_as_strategy")
                or (self.config.nine_loop.enabled and self.config.nine_loop.deepen_as_strategy)
            ):
                result.stages_skipped.append(stage)
                result.stage_metrics.append(
                    StageRunMetric(
                        stage=stage,
                        status="skipped",
                        message="deepen 已降级为策略（deepen_as_strategy），跳过独立阶段",
                    )
                )
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="deepen 已降级为策略（deepen_as_strategy），跳过独立阶段",
                )
                continue
            # Deepen 可选（默认启用；deepen.enabled=false 或 --skip deepen 关闭）
            if stage == "deepen" and not self.config.deepen.enabled:
                result.stages_skipped.append(stage)
                result.stage_metrics.append(
                    StageRunMetric(
                        stage=stage,
                        status="skipped",
                        message="deepen.enabled=false，跳过",
                    )
                )
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
                result.stage_metrics.append(
                    StageRunMetric(stage=stage, status="skipped", message="PDF 数据源，跳过深挖")
                )
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="PDF 数据源，跳过深挖",
                )
                continue

            out_dir, patterns = _stage_output(stage, topic_dir)
            if self.config.resume and self._stage_is_complete(
                stage, topic_dir, out_dir, patterns
            ):
                result.stages_skipped.append(stage)
                result.stage_metrics.append(
                    StageRunMetric(stage=stage, status="skipped", message="已有输出，跳过")
                )
                yield StageEvent(
                    stage=stage,
                    status="skipped",
                    progress=1.0,
                    message="已有输出，跳过",
                )
                if not self._completion_marker(topic_dir, stage).exists():
                    self._mark_stage_complete(topic_dir, stage)
                continue

            stage_start = time.monotonic()
            marker = self._completion_marker(topic_dir, stage)
            if marker.exists():
                marker.unlink()
            yield StageEvent(stage=stage, status="started", message=f"开始 {stage}")
            try:
                await self._exec_with_retry(stage, topic, topic_dir, result)
            except Exception as e:  # noqa: BLE001 - 记录失败并中断，保留已有输出
                duration = max(0.0, time.monotonic() - stage_start)
                result.failed_stage = stage
                message = f"{stage} 失败: {e}"
                result.stage_metrics.append(
                    StageRunMetric(
                        stage=stage,
                        status="failed",
                        duration_sec=duration,
                        message=message,
                    )
                )
                yield StageEvent(
                    stage=stage,
                    status="failed",
                    message=message,
                    data={"duration_sec": duration},
                )
                return  # 外层 stream 见 failed_stage 后会停止反向循环

            duration = max(0.0, time.monotonic() - stage_start)
            self._mark_stage_complete(topic_dir, stage)
            result.stages_completed.append(stage)
            result.stage_metrics.append(
                StageRunMetric(stage=stage, status="completed", duration_sec=duration)
            )
            yield StageEvent(
                stage=stage,
                status="completed",
                progress=1.0,
                message=f"{stage} 完成（{duration:.1f}s）",
                data={"duration_sec": duration},
            )

            # 阶段 F：organize 后可选 talk 关联（论文 → YouTube）；命中则重跑 clean→report
            if (
                stage == "organize"
                and self.config.talk.enabled
                and "report" in self.config.stages
            ):
                async for ev in self._run_talk_enrichment(topic, topic_dir, result):
                    yield ev
                if result.failed_stage:
                    return

    async def _run_talk_enrichment(
        self,
        topic: str,
        topic_dir: Path,
        result: PipelineResult,
    ) -> AsyncIterator[StageEvent]:
        """organize 后：TalkLinker 关联演讲视频；有新笔记则失效下游并重跑 clean→report。"""
        from .talk_linker import TalkLinker

        talk_start = time.monotonic()
        yield StageEvent(
            stage="talk",
            status="started",
            message=(
                f"talk 关联：max={self.config.talk.max_talks} "
                f"sim≥{self.config.talk.min_title_similarity} "
                f"ingest={self.config.talk.ingest}"
            ),
        )
        try:
            linker = TalkLinker(self.config.talk)
            report = await linker.enrich(topic_dir, topic=topic)
        except Exception as e:  # noqa: BLE001 — talk 失败不拖垮主管道
            duration = max(0.0, time.monotonic() - talk_start)
            message = f"talk 关联失败（继续 report）: {e}"
            result.stage_metrics.append(
                StageRunMetric(
                    stage="talk",
                    status="failed",
                    duration_sec=duration,
                    message=message,
                )
            )
            yield StageEvent(
                stage="talk",
                status="failed",
                message=message,
                data={"duration_sec": duration},
            )
            return

        msg = (
            f"候选 {report.candidates} 篇，命中 {len(report.matched)}，"
            f"跳过 {len(report.skipped)}，笔记 {len(report.files_written)}"
        )
        if report.warnings:
            msg += f"；warnings={len(report.warnings)}"
        talk_duration = max(0.0, time.monotonic() - talk_start)
        result.stage_metrics.append(
            StageRunMetric(
                stage="talk",
                status="completed",
                duration_sec=talk_duration,
                message=msg,
            )
        )
        yield StageEvent(
            stage="talk",
            status="completed",
            progress=1.0,
            message=msg,
            data={
                "matched": [
                    {
                        "paper": m.paper_title,
                        "url": m.video_url,
                        "confidence": m.confidence,
                    }
                    for m in report.matched
                ],
                "warnings": report.warnings,
                "duration_sec": talk_duration,
            },
        )

        if not report.files_written:
            return

        # 新 talk 笔记进入 raw/ → 失效 clean/extract/tree/report 并重跑（含 organize 以并入知识树）
        for sub in ("clean", "extracted", "tree"):
            d = topic_dir / sub
            if d.exists():
                shutil.rmtree(d)
        for stage in ("clean", "extract", "organize", "report"):
            marker = self._completion_marker(topic_dir, stage)
            if marker.exists():
                marker.unlink()
        for fname in ("report.md", "report.html"):
            f = topic_dir / fname
            if f.exists():
                f.unlink()

        # report 由外层正向循环生成一次，避免 enrichment 后重复生成。
        rerun = [s for s in ("clean", "extract", "organize") if s in self.config.stages]
        for stage in rerun:
            if stage == "extract" and not self.config.extractor.enabled:
                continue
            stage_start = time.monotonic()
            yield StageEvent(
                stage=stage,
                status="started",
                message=f"talk 后重跑 {stage}",
            )
            try:
                await self._exec_with_retry(stage, topic, topic_dir, result)
            except Exception as e:  # noqa: BLE001
                duration = max(0.0, time.monotonic() - stage_start)
                result.failed_stage = stage
                message = f"talk 后 {stage} 失败: {e}"
                result.stage_metrics.append(
                    StageRunMetric(
                        stage=stage,
                        status="failed",
                        duration_sec=duration,
                        message=message,
                    )
                )
                yield StageEvent(
                    stage=stage,
                    status="failed",
                    message=message,
                    data={"duration_sec": duration},
                )
                return
            duration = max(0.0, time.monotonic() - stage_start)
            self._mark_stage_complete(topic_dir, stage)
            if stage not in result.stages_completed:
                result.stages_completed.append(stage)
            result.stage_metrics.append(
                StageRunMetric(stage=stage, status="completed", duration_sec=duration)
            )
            yield StageEvent(
                stage=stage,
                status="completed",
                progress=1.0,
                message=f"talk 后 {stage} 完成（{duration:.1f}s）",
                data={"duration_sec": duration},
            )

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
        for stage in ("deepen", "clean", "extract", "organize", "report"):
            marker = ResearchPipeline._completion_marker(topic_dir, stage)
            if marker.exists():
                marker.unlink()
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
                    Path(self.config.pdf_dir), topic_dir
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
            report_input = topic_dir / ("clean" if self.config.mode == "brief" else "tree")
            result.report_result = await Reporter(self.config.reporter).run(
                report_input, self._get_llm(), topic
            )
        else:
            raise StageError("pipeline", f"未知 stage: {stage}")


def create_pipeline(config: PipelineConfig) -> ResearchPipeline:
    """工厂函数（03 §1 方式3）。"""
    return ResearchPipeline(config)
