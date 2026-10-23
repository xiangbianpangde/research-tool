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
from ..domain.errors import LLMAuthenticationError, LLMError, StageError, find_llm_diagnostic
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        self._started_at = ""
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
        self._started_at = _utc_now()
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
                diagnostic = find_llm_diagnostic(exc)
                self._result.stage_metrics.append(StageRunMetric(
                    stage=self._active, status="failed", message=str(exc),
                    diagnostic=diagnostic))
                data = (
                    {"diagnostic": diagnostic.model_dump(exclude_none=True)} if diagnostic else None
                )
                yield StageEvent(stage=self._active, status="failed", message=str(exc), data=data)
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
        if self.config.reporter.grounding_policy != "model_support":
            # Report-time reader code is research semantics, not a refresh side effect.
            from ..infrastructure.retrieval import ResearchCtlClient
            cfg = self.config.retrieval
            self._grounding_reader_identity = ResearchCtlClient(
                self.root, module_root=cfg.module_root or cfg.researchctl_root,
                python_executable=cfg.python_executable,
            ).reader_identity()
            config["grounding_reader"] = self._grounding_reader_identity
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
        fingerprint = _hash(_bytes({"runtime_contract": 19, "topic": self.topic,
                                   "config": config, "inputs": inputs}))
        self._state = (self.runtime.load_or_init(fingerprint) if self.config.resume
                       else self.runtime.start_new(fingerprint))
        self.run_root = self.root / "runs" / self._state["run_id"]
        for key, record in self._state["completed_stages"].items():
            envelope = read_json(self.root / record["artifact_path"])
            try:
                self._verify_files(envelope.get("files", {}))
            except RuntimeStateError:
                if key in {'qgate:100', 'qgate:101'}:
                    self._active = 'report'
                raise

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
                                    max_total=self.config.nine_loop.max_total_findings,
                                    evidence_policy=self.config.reporter.evidence_policy,
                                    final_round=round_no == self.config.nine_loop.max_rounds)
            async for event in self._step(f"qgate:{round_no}", lambda: decision):
                yield event
            decision = self._payload(f"qgate:{round_no}")
            if decision["verdict"] == "STOP_SUCCESS":
                for stage in ("targeted", "merge"):
                    if stage not in self._result.stages_completed:
                        self._result.stages_skipped.append(stage)
                        yield StageEvent(stage=stage, status="skipped", message="无需补搜")
                async for event in self._step("report", lambda: self._report(graph, inspection)):
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
                diagnostic = find_llm_diagnostic(exc)
                semantic_failure = diagnostic is not None and diagnostic.reason in {
                    "selection_invalid", "quote_mismatch", "content_filter",
                }
                if (
                    semantic_failure
                    or not isinstance(exc.__cause__, LLMError)
                    or attempt >= attempts
                ):
                    raise
                await self._retry_delay(attempt)
            except LLMError as exc:
                diagnostic = find_llm_diagnostic(exc)
                if (attempt >= attempts
                        or diagnostic is not None and diagnostic.reason == "content_filter"):
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
        if stage in self._result.stages_skipped:
            self._result.stages_skipped.remove(stage)
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

    async def _report(
        self, graph: dict[str, Any], inspection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle = None
        snapshot = None
        if self.config.reporter.grounding_policy != "model_support":
            try:
                graph, bundle, snapshot = await self._ground_evidence(graph, inspection)
            finally:
                self._active = "report"
        report = await assemble_report(
            graph, self._get_llm(), self.topic, self.config.reporter.max_length,
            style=self.config.reporter.style, evidence_policy=self.config.reporter.evidence_policy,
            inspection=inspection, minimum_sources=self.config.organizer.min_evidence_per_node,
            known_limitations=self.config.reporter.known_limitations,
            max_selected_claims=self.config.reporter.max_selected_claims,
            grounding_policy=self.config.reporter.grounding_policy, grounding_bundle=bundle,
            grounding_audit_callback=lambda rows: _atomic(
                self.run_root / 'grounding-attempts.json', _bytes({'attempts': rows}),
            ),
        )
        final = self.run_root / "final"
        files = materialize_graph(graph, final, self.topic)
        files += [final / "tree" / "00-主表.md", final / "tree" / "knowledge.json"]
        sources_file = final / "sources.json"
        _atomic(sources_file, _bytes(graph["sources"]))
        files.append(sources_file)
        if "evidence_audit" in report:
            audit_file = final / "evidence-audit.json"
            _atomic(audit_file, _bytes(report["evidence_audit"]))
            files.append(audit_file)
        if "grounding_audit" in report:
            grounding_file = final / "grounding-audit.json"
            _atomic(grounding_file, _bytes(report["grounding_audit"]))
            files.append(grounding_file)
        if snapshot is not None:
            files.extend([snapshot, self.run_root / "evidence-initial.json"])
        report_file = self.run_root / "final" / "report.md"
        _atomic(report_file, report["markdown"].encode("utf-8"))
        return {**report, "output_files": [str(path) for path in files] + [str(report_file)]}

    async def _ground_evidence(self, graph, inspection):  # noqa: PLR0915 - CAS evidence contract
        from ..infrastructure.retrieval import ResearchCtlClient
        from ..infrastructure.retrieval.snapshot import write_evidence_snapshot
        from ..nine_loop.grounding import build_evidence_bundle, dependency_facts
        from ..nine_loop.grounded_synthesis import resolve_required_evidence

        cfg = self.config.retrieval
        client = ResearchCtlClient(
            self.root, module_root=cfg.module_root or cfg.researchctl_root,
            python_executable=cfg.python_executable, timeout_sec=cfg.timeout_sec,
        )
        if client.reader_identity() != self._grounding_reader_identity:
            raise StageError('report', 'evidence reader changed during research run')
        capabilities = await asyncio.to_thread(client.capabilities)
        if "evidence-snapshot/v1" not in capabilities.get("contracts", []):
            raise StageError("report", "ResearchCTL evidence-snapshot/v1 capability required")
        keys = [key for key in self._state["completed_stages"]
                if key.startswith("merge:") and key != "merge:100"]
        stage = max(keys, key=lambda key: int(key.split(":")[1])) if keys else "knowledge"
        initial = self.run_root / "evidence-initial.json"
        if "qgate:100" not in self._state['completed_stages']:
            # Only uncommitted preparation is disposable; rebuild it from pinned stages.
            initial.unlink(missing_ok=True)
        async for _ in self._step('qgate:100', lambda: {
            'snapshot': str(write_evidence_snapshot(
                self.root, self._state, stage, provisional=True,
            )), 'output_files': [str(initial)],
        }):
            pass
        self._verify_files(read_json(
            self.root / self._state['completed_stages']['qgate:100']['artifact_path'],
        )['files'])
        blocks, scopes = [], []
        offset = 0
        for _ in range(100):
            response = await asyncio.to_thread(client.evidence, initial, offset=offset, limit=100)
            if response.get("status") != "success":
                raise StageError("report", "ResearchCTL full-source evidence query failed closed")
            blocks.extend(response["results"])
            scopes.append(response["scope"])
            if not response["scope"]["truncated"]:
                break
            offset += len(response["results"])
        else:
            raise StageError("report", "full-source evidence query budget exhausted")
        added = dependency_facts(graph, blocks)
        if added and "merge:100" not in self._state["completed_stages"]:
            delta = {"facts": added, "sources": graph["sources"]}
            async for _ in self._step("merge:100", lambda: self._merge(graph, delta)):
                pass
        if "merge:100" in self._state["completed_stages"]:
            graph = self._payload("merge:100")["graph"]
            from ..nine_loop.research import inspect_context_dependencies
            async for _ in self._step("inspect:100", lambda: inspect_context_dependencies(
                graph, inspection, self.config.organizer.min_evidence_per_node,
            )):
                pass
            inspection.update(self._payload("inspect:100"))
            stage = "merge:100"
        snapshot = self.run_root / 'evidence-snapshot.json'
        if 'qgate:101' not in self._state['completed_stages']:
            snapshot.unlink(missing_ok=True)
        async for _ in self._step('qgate:101', lambda: {
            'snapshot': str(write_evidence_snapshot(self.root, self._state, stage)),
            'output_files': [str(snapshot)],
        }):
            pass
        self._verify_files(read_json(
            self.root / self._state['completed_stages']['qgate:101']['artifact_path'],
        )['files'])
        resolved = await asyncio.to_thread(client.evidence, snapshot, view="graph")
        if resolved.get("status") != "success" or resolved["results"][0]["graph"] != graph:
            raise StageError("report", "ResearchCTL graph identity check failed closed")
        bundle = build_evidence_bundle(graph, blocks, [])
        bundle['required_evidence'] = resolve_required_evidence(
            graph, self.config.reporter.required_source_evidence,
        )
        if client.reader_identity() != self._grounding_reader_identity:
            raise StageError('report', 'evidence reader changed during retrieval')
        bundle.update(reader_identity=self._grounding_reader_identity,
                      snapshot_sha256=_hash(snapshot.read_bytes()), retrieval_scopes=scopes,
                      source_blocks=blocks, findings=inspection["findings"],
                      original_conflicts_preserved=True)
        return graph, bundle, snapshot

    def _refresh_retrieval_index(self, summary: dict[str, Any]) -> None:
        """Optionally refresh the derived ResearchCTL index after run close."""
        if not self.config.retrieval.refresh_on_run:
            return
        started = _utc_now()
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
        client = None
        try:
            from ..infrastructure.retrieval import ResearchCtlClient

            client = ResearchCtlClient(
                self.config.work_dir,
                researchctl_root=(
                    self.config.retrieval.module_root
                    or self.config.retrieval.researchctl_root
                ),
                python_executable=self.config.retrieval.python_executable,
                db_path=self.config.retrieval.db_path,
                timeout_sec=self.config.retrieval.timeout_sec,
            )
            if hasattr(client, "refresh"):
                refreshed = client.refresh()
                indexed = refreshed["index"]
                generated = refreshed["navigation"]
            else:
                indexed = client.index()
                generated = client.generate_index()
            if str(indexed.get("status", "")).lower() not in {"success", "ok"}:
                raise RuntimeError("ResearchCTL index returned a non-success envelope")
            if str(generated.get("status", "")).lower() not in {"success", "ok"}:
                raise RuntimeError("ResearchCTL generate-index returned a non-success envelope")
            final_state = {
                **state,
                "status": "success",
                "finished_at": _utc_now(),
                "index": indexed,
                "navigation": generated,
            }
        except Exception as exc:  # noqa: BLE001 - derived refresh is non-fatal
            safe_error = (
                getattr(exc, "envelope", None)
                if isinstance(getattr(exc, "envelope", None), dict)
                else str(exc)[:500]
            )
            last_envelope = getattr(client, "last_envelope", None) if client is not None else None
            final_state = {
                **state,
                "status": "stale",
                "finished_at": _utc_now(),
                "error_type": type(exc).__name__,
                "error": safe_error,
                **(
                    {"envelope": last_envelope}
                    if isinstance(last_envelope, dict) else {}
                ),
            }
            logger.warning(
                "ResearchCTL 派生索引刷新失败 run_id=%s error_type=%s",
                summary.get("run_id"),
                type(exc).__name__,
            )
        _atomic(state_path, _bytes(final_state))

    def _canonical_final_files(self, *, successful: bool) -> dict[str, str]:
        if not successful or not self._state.get("run_id"):
            return {}
        final = self.run_root / "final"
        paths = {
            "report": final / "report.md",
            "sources": final / "sources.json",
            "main_table": final / "tree" / "00-主表.md",
            "knowledge": final / "tree" / "knowledge.json",
        }
        if not all(path.is_file() for path in paths.values()):
            return {}
        return {
            path.relative_to(self.root).as_posix(): _hash(path.read_bytes())
            for path in paths.values()
        }

    def _runtime_snapshot(self) -> dict[str, Any]:
        """Read the complete current runtime JSON without inventing fields."""
        state_path = self.root / "runtime-state.json"
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = self._state
        return dict(payload) if isinstance(payload, dict) else dict(self._state)

    def _write_run_record(
        self,
        *,
        verdict: str,
        pipeline_complete: bool,
        finished_at: str,
        files: dict[str, str],
    ) -> Path | None:
        run_id = self._state.get("run_id")
        input_fingerprint = self._state.get("input_fingerprint")
        if not isinstance(run_id, str) or not run_id:
            return None
        if not isinstance(input_fingerprint, str) or not input_fingerprint:
            return None
        record = {
            "version": 1,
            "run_id": run_id,
            "topic": self.topic,
            "topic_slug": self.root.name,
            "verdict": verdict,
            "pipeline_complete": pipeline_complete,
            "started_at": self._started_at,
            "finished_at": finished_at,
            "input_fingerprint": input_fingerprint,
            "output_contract": "nine-stage",
            "stages": list(NINE_STAGES),
            "stages_completed": list(self._result.stages_completed),
            "stages_skipped": list(self._result.stages_skipped),
            "failed_stage": self._result.failed_stage,
            "files": files,
            "runtime": self._runtime_snapshot(),
        }
        path = self.run_root / "run-record.json"
        _atomic(path, _bytes(record))
        return path

    def _summary(self) -> None:
        result = self._result
        assert result is not None
        result.elapsed_sec = max(0.0, time.monotonic() - self._started)
        finished_at = _utc_now()
        verdict = result.verdict if result.verdict in {
            "STOP_SUCCESS", "STOP_ERROR", "STOP_BUDGET", "STOP_CANCELLED"
        } else "STOP_ERROR"
        canonical_files = self._canonical_final_files(
            successful=verdict == "STOP_SUCCESS" and result.report_result is not None
        )
        pipeline_complete = bool(
            verdict == "STOP_SUCCESS" and result.report_result is not None
            and len(canonical_files) == 4
        )
        record_path = self._write_run_record(
            verdict=verdict,
            pipeline_complete=pipeline_complete,
            finished_at=finished_at,
            files=canonical_files,
        )
        summary_files = {}
        if verdict == "STOP_SUCCESS":
            final = self.run_root / "final"
            for path in sorted(final.rglob("*")):
                if path.is_file():
                    summary_files[path.relative_to(self.root).as_posix()] = _hash(path.read_bytes())
        summary = {"version": 1, "run_id": self._state.get("run_id"),
                   "topic": self.topic, "topic_slug": self.root.name,
                   "verdict": verdict, "rounds": result.rounds,
                   "output_contract": "nine-stage", "stages": list(NINE_STAGES),
                   "stages_completed": result.stages_completed,
                   "stages_skipped": result.stages_skipped,
                   "failed_stage": result.failed_stage,
                   "pipeline_complete": pipeline_complete,
                   "started_at": self._started_at, "finished_at": finished_at,
                   "input_fingerprint": self._state.get("input_fingerprint"),
                   "stage_metrics": [metric.model_dump(exclude_none=True)
                                     for metric in result.stage_metrics],
                   "files": summary_files,
                   "output_dir": (
                       (self.run_root / "final").relative_to(self.root).as_posix()
                       if pipeline_complete else None
                   ),
                   "record_path": (
                       record_path.relative_to(self.root).as_posix() if record_path else None
                   ),
                   "elapsed_sec": round(result.elapsed_sec, 3)}
        if "report" in self._state.get("completed_stages", {}):
            audit = self._payload("report").get("evidence_audit")
            if audit is not None:
                summary.update(
                    report_contract=audit["contract"],
                    strict_evidence_complete=audit["strict_evidence_complete"],
                    unresolved_finding_count=len(audit["unresolved_findings"]),
                )
        if self._state.get("run_id"):
            failures = [metric for metric in result.stage_metrics if metric.diagnostic is not None]
            if failures:
                metric = failures[-1]
                path = self.run_root / "failure-diagnostic.json"
                _atomic(path, _bytes({
                    "version": 1, "run_id": self._state["run_id"], "stage": metric.stage,
                    "diagnostic": metric.diagnostic.model_dump(exclude_none=True),
                }))
                summary["diagnostic_path"] = path.relative_to(self.root).as_posix()
        result.run_summary_path = self.root / "run-summary.json"
        _atomic(result.run_summary_path, _bytes(summary))
        if record_path is not None:
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
