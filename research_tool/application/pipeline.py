"""The single production nine-stage research dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import tempfile
import time
import threading
from datetime import datetime, timezone
from contextlib import aclosing
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from ..common.logging_config import get_logger
from ..common.slug import slugify
from ..domain.errors import LLMAuthenticationError, LLMError, StageError
from ..domain.models import (
    NINE_STAGES, PipelineConfig, PipelineResult, ReportResult, StageEvent, StageRunMetric,
)
from ..infrastructure.llm.base import LLMClient
from ..infrastructure.stages.base import read_json
from ..infrastructure.stages.cleaner import Cleaner
from ..infrastructure.stages.collector import Collector
from ..infrastructure.stages.extractor import Extractor
from ..infrastructure.stages.research_strategies import ResearchStrategies
from ..nine_loop.evidence import parse_source
from ..nine_loop.knowledge_min import build_evidence_graph
from ..nine_loop.merge_min import graph_digest, merge_evidence_graph
from ..nine_loop.research import (
    assemble_report, gate_decision, inspect_graph, materialize_graph, plan_targeted,
)
from ..nine_loop.runtime_state import RuntimeState, RuntimeStateError
from .talk_linker import TalkLinker

logger = get_logger(__name__)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ResearchPipeline:
    """Research through one bounded, persisted nine-stage evidence loop."""

    def __init__(self, config: PipelineConfig, *, llm: LLMClient | None = None) -> None:
        self.config = config
        self._llm = llm
        self._result: PipelineResult | None = None
        self._state: dict[str, Any] = {}
        self._active = "collect"
        self._started = 0.0
        self._execution_lock = threading.Lock()

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.from_config(self.config.llm)
        return self._llm

    async def run(self, topic: str | None = None) -> PipelineResult:
        async for _ in self.stream(topic):
            pass
        assert self._result is not None
        return self._result

    async def stream(self, topic: str | None = None) -> AsyncIterator[StageEvent]:
        if not self._execution_lock.acquire(blocking=False):
            raise StageError("pipeline", "此 Pipeline 实例已有运行，请使用独立实例")
        try:
            async with aclosing(self._stream(topic)) as events:
                async for event in events:
                    yield event
        finally:
            self._execution_lock.release()

    async def _stream(self, topic: str | None = None) -> AsyncIterator[StageEvent]:
        self._state = {}
        self._active = "collect"
        self.topic = (topic or self.config.topic).strip()
        if not self.topic:
            raise StageError("pipeline", "缺少 topic")
        self.root = (self.config.work_dir / slugify(self.topic)).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = RuntimeState(self.root)
        self._result = PipelineResult(topic_dir=self.root)
        self._started = time.monotonic()
        # A concurrent caller must not overwrite the first caller's summary.
        with self.runtime.run_lock():
            try:
                self._initialize()
                async for event in self._execute():
                    yield event
            except (asyncio.CancelledError, GeneratorExit):
                self._result.verdict = "STOP_CANCELLED"
                self._result.failed_stage = self._active
                raise
            except TimeoutError:
                self._result.verdict = "STOP_BUDGET"
                self._result.failed_stage = self._active
                self._result.stage_metrics.append(StageRunMetric(
                    stage=self._active, status="failed", message="运行时间预算耗尽"))
                yield StageEvent(stage=self._active, status="failed", message="运行时间预算耗尽")
            except Exception as exc:  # noqa: BLE001 - failed runs retain evidence, never publish
                self._result.failed_stage = self._active
                self._result.verdict = "STOP_ERROR"
                self._result.report_result = None
                self._result.stage_metrics.append(StageRunMetric(
                    stage=self._active, status="failed", message=str(exc)))
                yield StageEvent(stage=self._active, status="failed", message=str(exc))
            finally:
                self._summary()

    def _initialize(self) -> None:
        config = self.config.model_dump(mode="json", exclude={"resume", "work_dir"})
        # Credentials and transport-only session routing affect neither source
        # identity nor cached report semantics.
        config["llm"].pop("api_key", None)
        config["llm"].pop("session_id", None)
        for key in ("tavily_api_key", "semantic_scholar_api_key", "github_token", "proxy"):
            config["collector"].pop(key, None)
        # Retrieval refresh is a derived post-run side effect; changing its
        # transport/location must not create a different research identity.
        config.pop("retrieval", None)
        inputs = {}
        if self.config.input_dir:
            path = Path(self.config.input_dir).resolve()
            if not path.is_dir():
                raise StageError("collect", "输入目录不存在")
            inputs[str(path)] = {p.name: _hash(p.read_bytes())
                                 for p in sorted(path.glob("*.md")) if p.is_file()}
        if self.config.pdf_dir:
            from ..infrastructure.ingest.pdf import _collect_pdfs
            path = Path(self.config.pdf_dir).resolve()
            inputs[str(path)] = {p.name: _hash(p.read_bytes()) for p in _collect_pdfs(path)}
        fingerprint = _hash(_bytes({"runtime_contract": 4, "topic": self.topic,
                                   "config": config, "inputs": inputs}))
        self._state = (self.runtime.load_or_init(fingerprint) if self.config.resume
                       else self.runtime.start_new(fingerprint))
        self.run_root = self.root / "runs" / self._state["run_id"]
        for record in self._state["completed_stages"].values():
            envelope = read_json(self.root / record["artifact_path"])
            self._verify_files(envelope.get("files", {}))

    async def _execute(self) -> AsyncIterator[StageEvent]:
        for key, action in (
            ("collect", self._collect),
            ("clean", self._clean_initial),
            ("extract", self._extract_initial),
            ("knowledge", self._knowledge),
        ):
            async for event in self._step(key, action):
                yield event
        graph = self._payload("knowledge")["graph"]
        previous = None
        new_claim_ids: set[str] = set()
        for round_no in range(self.config.nine_loop.max_rounds + 1):
            self._result.rounds = round_no
            async for event in self._step(
                f"inspect:{round_no}", lambda: self._inspect(graph, previous, new_claim_ids)
            ):
                yield event
            inspection = self._payload(f"inspect:{round_no}")
            decision = gate_decision(inspection,
                                    max_high=self.config.nine_loop.max_high_findings,
                                    max_total=self.config.nine_loop.max_total_findings)
            async for event in self._step(f"qgate:{round_no}", lambda: decision):
                yield event
            decision = self._payload(f"qgate:{round_no}")
            if decision["verdict"] == "STOP_SUCCESS":
                for stage in ("targeted", "merge"):
                    if stage not in self._result.stages_completed:
                        self._result.stages_skipped.append(stage)
                        yield StageEvent(stage=stage, status="skipped", message="无需补搜")
                async for event in self._step("report", lambda: self._report(graph)):
                    yield event
                report = self._payload("report")
                self._result.report_result = ReportResult(
                    report_path=self.run_root / "final" / "report.md",
                    word_count=len(report["markdown"]),
                    source_count=len(graph["sources"]))
                self._result.verdict = "STOP_SUCCESS"
                return
            if round_no == self.config.nine_loop.max_rounds:
                self._result.verdict = "STOP_BUDGET"
                return
            async for event in self._step(
                f"targeted:{round_no}", lambda: self._targeted(inspection, round_no)
            ):
                yield event
            delta = self._payload(f"targeted:{round_no}")
            async for event in self._step(f"merge:{round_no}", lambda: self._merge(graph, delta)):
                yield event
            merged = self._payload(f"merge:{round_no}")["graph"]
            new_claim_ids = ({fact["claim_id"] for fact in merged["facts"]}
                             - {fact["claim_id"] for fact in graph["facts"]})
            previous, graph = inspection, merged

    async def _step(self, key: str, action: Callable) -> AsyncIterator[StageEvent]:
        stage = key.split(":")[0]
        self._active = stage
        if key in self._state["completed_stages"]:
            if stage not in self._result.stages_completed:
                self._result.stages_completed.append(stage)
            self._result.stage_metrics.append(StageRunMetric(
                stage=stage, status="skipped", message="已核验持久产物"))
            yield StageEvent(stage=stage, status="skipped", message="已核验持久产物",
                             data={"duration_sec": 0.0, "request_id": key})
            return
        if time.monotonic() - self._started >= self.config.nine_loop.wall_s_max:
            raise TimeoutError("运行时间预算耗尽")
        yield StageEvent(stage=stage, status="started", message=f"开始 {stage}")
        started = time.monotonic()
        attempts = (
            self.config.llm_stage_attempts if stage in {"extract", "inspect", "report"} else 1
        )
        payload = None
        for attempt in range(1, attempts + 1):
            try:
                async with asyncio.timeout(self._remaining()):
                    if self._stage_uses_llm(stage):
                        await self._get_llm().healthcheck(
                            timeout_sec=min(
                                self.config.llm_healthcheck_timeout_sec, self._remaining(),
                            )
                        )
                    self._remaining()
                    value = action()
                    payload = await value if inspect.isawaitable(value) else value
                    self._remaining()
                break
            except LLMAuthenticationError:
                raise
            except StageError as exc:
                if not isinstance(exc.__cause__, LLMError) or attempt >= attempts:
                    raise
                await self._retry_delay(attempt)
            except LLMError:
                if attempt >= attempts:
                    raise
                await self._retry_delay(attempt)
        assert payload is not None
        envelope = {
            "v": 1, "run_id": self._state["run_id"], "stage": stage,
            "request_id": key, "idempotency_key": _hash(_bytes({
                "input": self._state["input_fingerprint"], "key": key,
                "previous": self._state["latest_digest"]})),
            "budget_lease": {"wall_s_max": self.config.nine_loop.wall_s_max},
            "result": payload, "error": None,
            "files": self._output_files(payload),
        }
        artifact = (
            self.root / "artifacts" / self._state["run_id"]
            / (key.replace(":", "-") + ".json")
        )
        _atomic(artifact, _bytes(envelope))
        self._state = self.runtime.commit_stage(
            key, artifact, _hash(artifact.read_bytes()), self._state["latest_digest"])
        if stage not in self._result.stages_completed:
            self._result.stages_completed.append(stage)
        self._result.stage_metrics.append(StageRunMetric(
            stage=stage, status="completed", duration_sec=time.monotonic() - started))
        yield StageEvent(stage=stage, status="completed", progress=1.0, message=f"{stage} 完成",
                         data={"duration_sec": time.monotonic() - started, "request_id": key})

    def _remaining(self) -> float:
        remaining = self.config.nine_loop.wall_s_max - (time.monotonic() - self._started)
        if remaining <= 0:
            raise TimeoutError("运行时间预算耗尽")
        return remaining

    async def _retry_delay(self, attempt: int) -> None:
        remaining = self._remaining()
        await asyncio.sleep(min(self.config.llm_retry_backoff_sec * attempt, remaining))
        self._remaining()

    def _stage_uses_llm(self, stage: str) -> bool:
        if stage == "collect":
            return bool(
                self.config.pdf_dir and self.config.pdf_ingest.translate
                or not (self.config.pdf_dir or self.config.input_dir)
                and (self.config.collector.strategies.enabled
                     or self.config.collector.llm_query_expansion)
            )
        if stage == "clean":
            return self.config.cleaner.relevance_filter
        return stage in {"extract", "inspect", "targeted", "report"}

    def _payload(self, key: str) -> dict[str, Any]:
        record = self._state["completed_stages"][key]
        envelope = read_json(self.root / record["artifact_path"])
        return envelope["result"]

    def _output_files(self, payload: dict[str, Any]) -> dict[str, str]:
        paths = [Path(path) for path in payload.get("output_files", [])]
        result = {}
        for path in paths:
            relative = path.resolve().relative_to(self.root)
            result[relative.as_posix()] = _hash(path.read_bytes())
        return result

    def _verify_files(self, files: dict[str, str]) -> None:
        for relative, digest in files.items():
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise RuntimeStateError("缺失已提交的阶段产物；拒绝跳过")
            if _hash(path.read_bytes()) != digest:
                raise RuntimeStateError("阶段产物内容变化；拒绝使用旧缓存")

    def _documents(
        self, directory: Path, *, allow_empty: bool = False
    ) -> list[dict[str, Any]]:
        documents = []
        for path in sorted(directory.glob("*.md")):
            source = parse_source(path.read_text(encoding="utf-8"))
            if source.get("evidence_kind", "body") != "body":
                continue
            if not source["locator"] or not source["text"].strip():
                raise StageError(self._active, "来源缺少实际正文或定位信息")
            source["path"] = str(path)
            documents.append(source)
        if not documents and not allow_empty:
            raise StageError(self._active, "没有可用的正文资料")
        return documents

    async def _collect(self) -> dict[str, Any]:
        work = self.run_root / "initial"
        raw = work / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        if self.config.input_dir:
            for path in sorted(Path(self.config.input_dir).glob("*.md")):
                shutil.copyfile(path, raw / path.name)
        elif self.config.pdf_dir:
            from ..infrastructure.ingest import PdfIngestor
            await PdfIngestor(self.config.pdf_ingest, self._get_llm()
                             if self.config.pdf_ingest.translate else None).run(
                                 Path(self.config.pdf_dir), work)
        else:
            collector = Collector(
                self.config.collector,
                self._get_llm() if self.config.collector.llm_query_expansion else None,
            )
            self._result.collect_result = await collector.run(self.topic, work)
            if self.config.collector.strategies.enabled:
                await ResearchStrategies(
                    self.config.collector.strategies, collector, self._get_llm()
                ).collect_more(self.topic, raw, core_keyword=self.config.collector.core_keyword)
        talk = None
        if self.config.talk.enabled:
            enriched = await TalkLinker(self.config.talk).enrich(work, self.topic, force=True)
            talk = {"candidates": enriched.candidates,
                    "matched": len(enriched.matched), "warnings": enriched.warnings,
                    "files": [str(path) for path in enriched.files_written]}
        documents = self._documents(raw)
        return {"documents": documents, "directory": str(work), "talk": talk,
                "output_files": [str(path) for path in sorted(raw.glob("*")) if path.is_file()]}

    async def _clean_initial(self) -> dict[str, Any]:
        return await self._clean_work(Path(self._payload("collect")["directory"]))

    async def _clean_work(self, work: Path) -> dict[str, Any]:
        cleaner = Cleaner(self.config.cleaner)
        cleaned = cleaner.process(work / "raw", work, evidence_kinds={"body"})
        if self.config.cleaner.relevance_filter:
            cleaned = await cleaner.filter_relevance(cleaned, self._get_llm(), self.topic)
        for path in list(cleaned.files):
            if (
                len(parse_source(path.read_text(encoding="utf-8"))["text"].strip())
                < self.config.cleaner.min_content_length
            ):
                path.unlink()
        documents = self._documents(work / "clean")
        return {
            "documents": documents,
            "directory": str(work),
            "output_files": [
                str(path) for path in sorted((work / "clean").glob("*")) if path.is_file()
            ],
        }

    async def _extract_initial(self) -> dict[str, Any]:
        return await self._extract_work(Path(self._payload("clean")["directory"]))

    async def _extract_work(self, work: Path) -> dict[str, Any]:
        config = self.config.extractor.model_copy(
            update={"enabled": True, "fail_on_chunk_error": True}
        )
        extracted = await Extractor(config).run(work / "clean", self._get_llm(), work)
        if not extracted.claims:
            raise StageError("extract", "未抽取出有正文证据的事实")
        return {"facts": extracted.claims,
                "output_files": [str(path) for path in sorted((work / "extracted").glob("*.json"))]}

    def _knowledge(self) -> dict[str, Any]:
        graph = build_evidence_graph(self._payload("extract")["facts"],
                                     self._payload("clean")["documents"], title=self.topic)
        return {"graph": graph}

    async def _inspect(
        self, graph: dict[str, Any], previous: dict[str, Any] | None, new_claim_ids: set[str],
    ) -> dict[str, Any]:
        return await inspect_graph(graph, self._get_llm(), self.topic,
                                   self.config.organizer.min_evidence_per_node,
                                   previous=previous, new_claim_ids=new_claim_ids)

    async def _targeted(self, inspection: dict[str, Any], round_no: int) -> dict[str, Any]:
        queries = plan_targeted(inspection)
        if not queries:
            raise StageError("targeted", "闸门要求补搜但没有有效查询")
        work = self.run_root / f"targeted-{round_no}"
        collector = Collector(self.config.collector)
        hits = await collector.search_queries(queries)
        await collector.fetch_and_store(self.topic, hits.hits, work / "raw")
        raw_files = [
            str(path) for path in sorted((work / "raw").glob("*"))
            if path.is_file()
        ]
        documents = self._documents(work / "raw", allow_empty=True)
        if not documents:
            # Search/fetch attempts without usable body evidence are retained as
            # an empty delta. They must not satisfy or close an evidence gap.
            return {
                "queries": queries,
                "sources": [],
                "facts": [],
                "warnings": ["补搜未获得可核验正文；摘要/导航页未进入证据账本"],
                "output_files": raw_files,
            }
        cleaned = await self._clean_work(work)
        extracted = await self._extract_work(work)
        return {
            "queries": queries,
            "sources": cleaned["documents"],
            "facts": extracted["facts"],
            "output_files": [*cleaned["output_files"], *extracted["output_files"], *raw_files],
        }

    def _merge(self, graph: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
        return merge_evidence_graph(graph, delta, graph_digest(graph))

    async def _report(self, graph: dict[str, Any]) -> dict[str, Any]:
        report = await assemble_report(graph, self._get_llm(), self.topic,
                                       self.config.reporter.max_length,
                                       style=self.config.reporter.style)
        final = self.run_root / "final"
        files = materialize_graph(graph, final, self.topic)
        files += [final / "tree" / "00-主表.md", final / "tree" / "knowledge.json"]
        sources_file = final / "sources.json"
        _atomic(sources_file, _bytes(graph["sources"]))
        files.append(sources_file)
        report_file = self.run_root / "final" / "report.md"
        _atomic(report_file, report["markdown"].encode("utf-8"))
        return {**report, "output_files": [str(path) for path in files] + [str(report_file)]}

    def _refresh_retrieval_index(self, summary: dict[str, Any]) -> None:
        """Optionally refresh the derived ResearchCTL index after run close."""
        if not self.config.retrieval.refresh_on_run:
            return
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state = {
            "version": 1,
            "status": "refreshing",
            "started_at": started,
            "run_id": summary.get("run_id"),
            "verdict": summary.get("verdict"),
        }
        state_dir = self.root / ".index" / "retrieval-refresh"
        state_path = state_dir / f"{self.root.name}.json"
        try:
            _atomic(state_path, _bytes(state))
        except OSError as exc:
            logger.warning(
                "写入 ResearchCTL 刷新状态失败 run_id=%s error_type=%s",
                summary.get("run_id"),
                type(exc).__name__,
            )
            return
        try:
            from ..infrastructure.retrieval import ResearchCtlClient

            client = ResearchCtlClient(
                self.config.work_dir,
                researchctl_root=self.config.retrieval.researchctl_root,
                python_executable=self.config.retrieval.python_executable,
                db_path=self.config.retrieval.db_path,
                timeout_sec=self.config.retrieval.timeout_sec,
            )
            indexed = client.index()
            generated = client.generate_index()
            final_state = {
                **state,
                "status": "success",
                "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "index": indexed,
                "navigation": generated,
            }
        except Exception as exc:  # noqa: BLE001 - derived refresh is non-fatal
            final_state = {
                **state,
                "status": "stale",
                "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
            logger.warning(
                "ResearchCTL 派生索引刷新失败 run_id=%s error_type=%s",
                summary.get("run_id"),
                type(exc).__name__,
            )
        _atomic(state_path, _bytes(final_state))

    def _summary(self) -> None:
        result = self._result
        assert result is not None
        result.elapsed_sec = max(0.0, time.monotonic() - self._started)
        files = {}
        if result.verdict == "STOP_SUCCESS":
            final = self.run_root / "final"
            for path in [final / "report.md", final / "sources.json",
                         *sorted((final / "tree").glob("*"))]:
                if path.is_file():
                    files[path.relative_to(self.root).as_posix()] = _hash(path.read_bytes())
        summary = {"version": 1, "run_id": self._state.get("run_id"),
                   "topic": self.topic, "topic_slug": self.root.name,
                   "verdict": result.verdict, "rounds": result.rounds,
                   "output_contract": "nine-stage", "stages": list(NINE_STAGES),
                   "stages_completed": result.stages_completed,
                   "stages_skipped": result.stages_skipped,
                   "failed_stage": result.failed_stage,
                   "pipeline_complete": (
                       result.verdict == "STOP_SUCCESS" and result.report_result is not None
                   ),
                   "stage_metrics": [metric.model_dump() for metric in result.stage_metrics],
                   "files": files,
                   "output_dir": (
                       (self.run_root / "final").relative_to(self.root).as_posix()
                       if result.verdict == "STOP_SUCCESS" else None
                   ),
                   "elapsed_sec": round(result.elapsed_sec, 3)}
        result.run_summary_path = self.root / "run-summary.json"
        _atomic(result.run_summary_path, _bytes(summary))
        try:
            from ..infrastructure.retrieval import write_retrieval_manifest

            write_retrieval_manifest(self.root, summary)
        except (OSError, ValueError, TypeError) as exc:
            # The manifest is a derived retrieval projection.  Its failure must
            # remain observable without changing the canonical nine-stage verdict.
            logger.warning(
                "写入 ResearchCTL retrieval manifest 失败 run_id=%s error_type=%s",
                summary.get("run_id"),
                type(exc).__name__,
            )
        self._refresh_retrieval_index(summary)


def create_pipeline(config: PipelineConfig, *, llm: LLMClient | None = None) -> ResearchPipeline:
    return ResearchPipeline(config, llm=llm)
