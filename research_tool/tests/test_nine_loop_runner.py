"""Nine-stage behavior through ResearchPipeline with only I/O boundaries faked."""

from __future__ import annotations

import json
import socket
import asyncio

import pytest

from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import CleanerConfig, CollectorConfig, OrganizerConfig, PipelineConfig
from research_tool.domain.errors import LLMAuthenticationError, LLMError
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.infrastructure.search.base import SearchHit
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher

QUOTE = "The measured battery retained 90 percent capacity after 1000 cycles."
SECOND = "The independent test confirmed 90 percent capacity after 1000 cycles."


@pytest.fixture
def external_io(monkeypatch):
    calls = []

    class Backend:
        async def search(self, query, *args, **kwargs):
            calls.append(query)
            suffix = "b" if "independent evidence" in query else "a"
            return [SearchHit(url=f"https://papers.test/{suffix}", title="battery", source_engine="web")]

    async def fetch(self, url):
        text = QUOTE if url.endswith("/a") else SECOND
        return FetchResult(url, text + "\n\n" + "Experimental methods and limitations. " * 8, ok=True)

    monkeypatch.setattr("research_tool.infrastructure.stages.collector.get_backend", lambda *a: Backend())
    monkeypatch.setattr(Fetcher, "fetch", fetch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    return calls


def model_reply(prompt, schema):
    if schema.__name__ == "ResearchQueries":
        return schema(entities=["battery"], queries=["battery cycle life experimental evidence"])
    if schema.__name__ == "_ChunkResult":
        quote = SECOND if SECOND in prompt else QUOTE
        return schema(claims=[{"text": quote, "quote": quote, "topic": "Cycle life"}])
    if schema.__name__ == "ResearchInspection":
        return schema(findings=[])
    if schema.__name__ == "ReportOutline":
        payload = json.loads(prompt.split("DATA:\n", 1)[1])
        return schema(sections=[{"heading": "Cycle life", "claim_ids": [x["claim_id"] for x in payload["facts"]]}])
    if schema.__name__ == "ReportSectionDraft":
        facts = json.loads(prompt.split("DATA:\n", 1)[1])
        return schema(paragraphs=[{"text": fact["text"], "claim_ids": [fact["claim_id"]]}
                                  for fact in facts])
    if schema.__name__ == "ReportVerification":
        payload = json.loads(prompt.split("DATA:\n", 1)[1])
        return schema(checks=[{"paragraph_index": i, "supported": True, "reason": "quotes match"}
                              for i in range(len(payload["paragraphs"]))])
    if schema.__name__ == "_Scores":
        return schema(scores=[1.0])
    return schema()


def config(tmp_path, *, minimum=1):
    return PipelineConfig(
        topic="battery", work_dir=tmp_path, resume=True,
        collector=CollectorConfig(search_engines=["web"], search_cache=False, min_doc_chars=20,
                                  search_relevance_min_overlap=0, save_originals=False),
        cleaner=CleanerConfig(min_content_length=20, dedup_similarity=1.0),
        organizer=OrganizerConfig(min_nodes=1, max_nodes=3, min_evidence_per_node=minimum),
    )


@pytest.mark.asyncio
async def test_topic_to_grounded_report_uses_unique_nine_stage_contract(tmp_path, external_io):
    pipe = ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=model_reply))
    events = [event async for event in pipe.stream()]
    result = pipe._result

    assert result.failed_stage is None
    report = result.report_result.report_path.read_text()
    assert QUOTE in report
    assert "https://papers.test/a" in report
    summary = json.loads(result.run_summary_path.read_text())
    assert summary["verdict"] == "STOP_SUCCESS"
    assert summary["pipeline_complete"] is True
    assert summary["stages"] == ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]
    assert not any(x.stage in {"deepen", "organize", "network", "gate"} for x in events)
    assert {x.stage for x in events} == set(summary["stages"])
    assert external_io


@pytest.mark.asyncio
async def test_continue_fetches_cleans_extracts_merges_and_reinspects(tmp_path, external_io):
    result = await ResearchPipeline(
        config(tmp_path, minimum=2), llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.failed_stage is None
    summary = json.loads(result.run_summary_path.read_text())
    assert summary["verdict"] == "STOP_SUCCESS"
    assert summary["rounds"] == 1
    assert len(external_io) >= 2
    report = result.report_result.report_path.read_text()
    assert QUOTE in report and SECOND in report
    assert "targeted" in result.stages_completed and "merge" in result.stages_completed


@pytest.mark.asyncio
async def test_resume_reuses_verified_outputs_without_model_or_search_calls(tmp_path, external_io):
    first = await ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=model_reply)).run()
    before = len(external_io)
    llm = MockLLMClient(structured_response=lambda *args: pytest.fail("resume called model"))

    resumed = await ResearchPipeline(config(tmp_path), llm=llm).run()

    assert resumed.failed_stage is None
    assert resumed.report_result.report_path.read_bytes() == first.report_result.report_path.read_bytes()
    assert len(external_io) == before
    assert llm.calls == []


@pytest.mark.asyncio
async def test_targeted_without_body_evidence_keeps_gap_and_stops_budget(tmp_path, external_io, monkeypatch):
    original_fetch = Fetcher.fetch

    async def empty_targeted_fetch(self, url):
        if url.endswith("/b"):
            return FetchResult(url, "", ok=False, error="abstract-only")
        return await original_fetch(self, url)

    monkeypatch.setattr(Fetcher, "fetch", empty_targeted_fetch)
    cfg = config(tmp_path, minimum=2)
    cfg.nine_loop.max_rounds = 1
    result = await ResearchPipeline(
        cfg, llm=MockLLMClient(structured_response=model_reply)
    ).run()

    assert result.verdict == "STOP_BUDGET"
    assert result.report_result is None
    assert "targeted" in result.stages_completed
    assert "merge" in result.stages_completed
    assert json.loads(result.run_summary_path.read_text())["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_ungrounded_model_fact_stops_before_report(tmp_path, external_io):
    def hallucination(prompt, schema):
        if schema.__name__ == "_ChunkResult":
            return schema(claims=[{"text": "invented", "quote": "absent from source", "topic": "Cycle life"}])
        return model_reply(prompt, schema)

    result = await ResearchPipeline(config(tmp_path), llm=MockLLMClient(structured_response=hallucination)).run()

    assert result.failed_stage == "extract"
    assert result.report_result is None
    assert not list(result.topic_dir.glob("report.*"))
    assert json.loads(result.run_summary_path.read_text())["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_budget_exhaustion_retains_continue_without_publishing(tmp_path, external_io):
    cfg = config(tmp_path, minimum=2)
    cfg.nine_loop.max_rounds = 0
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_BUDGET"
    assert result.report_result is None
    assert not (result.topic_dir / "report.md").exists()
    assert json.loads(result.run_summary_path.read_text())["pipeline_complete"] is False


@pytest.mark.asyncio
async def test_missing_and_modified_committed_outputs_are_never_resumed_as_complete(tmp_path, external_io):
    cfg = config(tmp_path)
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    source = next((result.topic_dir / "runs").glob("*/initial/raw/*.md"))
    source.write_text("altered source", encoding="utf-8")
    calls = len(external_io)
    resumed = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert resumed.verdict == "STOP_ERROR" and resumed.report_result is None
    assert len(external_io) == calls


@pytest.mark.asyncio
async def test_changed_local_input_starts_new_run_and_preserves_prior_report(tmp_path):
    raw = tmp_path / "inputs"
    raw.mkdir()
    source = raw / "01.md"
    source.write_text("<!-- source: file:///approved/source.md -->\n\n" + QUOTE, encoding="utf-8")
    cfg = config(tmp_path / "out")
    cfg.input_dir = raw
    first = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    old_id = json.loads(first.run_summary_path.read_text())["run_id"]
    source.write_text("<!-- source: file:///approved/source.md -->\n\n" + SECOND, encoding="utf-8")
    second = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    summary = json.loads(second.run_summary_path.read_text())
    assert summary["run_id"] != old_id
    assert SECOND in second.report_result.report_path.read_text()
    assert len(list((second.topic_dir / "runs").glob("*/final/report.md"))) == 2


@pytest.mark.asyncio
async def test_transient_model_error_retries_but_authentication_does_not(tmp_path, external_io):
    class Flaky(MockLLMClient):
        attempts = 0
        async def chat_structured(self, prompt, schema, system=None):
            if schema.__name__ == "_ChunkResult":
                self.attempts += 1
                if self.attempts == 1:
                    raise LLMError("transient timeout")
            return model_reply(prompt, schema)

    cfg = config(tmp_path)
    cfg.llm_retry_backoff_sec = 0
    model = Flaky()
    result = await ResearchPipeline(cfg, llm=model).run()
    assert result.verdict == "STOP_SUCCESS" and model.attempts == 2

    class BadKey(Flaky):
        async def chat_structured(self, prompt, schema, system=None):
            self.attempts += 1
            raise LLMAuthenticationError("invalid key")
    cfg.resume = False
    bad = BadKey()
    result = await ResearchPipeline(cfg, llm=bad).run()
    assert result.verdict == "STOP_ERROR" and bad.attempts == 1


@pytest.mark.asyncio
async def test_cancellation_releases_run_lock_and_records_incomplete(tmp_path, external_io):
    class Waiting(MockLLMClient):
        entered = asyncio.Event()
        async def chat_structured(self, prompt, schema, system=None):
            self.entered.set()
            await asyncio.Event().wait()
    model = Waiting()
    pipe = ResearchPipeline(config(tmp_path), llm=model)
    task = asyncio.create_task(pipe.run())
    await model.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert json.loads(pipe._result.run_summary_path.read_text())["verdict"] == "STOP_CANCELLED"
    from research_tool.nine_loop.runtime_state import RuntimeState
    with RuntimeState(pipe._result.topic_dir).run_lock():
        pass


@pytest.mark.asyncio
async def test_report_support_rejection_blocks_publishing(tmp_path, external_io):
    def unsupported(prompt, schema):
        if schema.__name__ == "ReportVerification":
            return schema(checks=[{"paragraph_index": 0, "supported": False,
                                   "reason": "conclusion exceeds source"}])
        return model_reply(prompt, schema)
    result = await ResearchPipeline(
        config(tmp_path), llm=MockLLMClient(structured_response=unsupported)
    ).run()
    assert result.failed_stage == "report" and result.verdict == "STOP_ERROR"
    assert result.report_result is None and not (result.topic_dir / "report.md").exists()


@pytest.mark.asyncio
async def test_report_support_failure_gets_one_grounded_repair(tmp_path, external_io):
    verification_calls = 0
    repair_calls = 0
    draft_calls = 0

    def reply(prompt, schema):
        nonlocal verification_calls, repair_calls, draft_calls
        if schema.__name__ == "ReportSectionDraft":
            draft_calls += 1
            facts = json.loads(prompt.split("DATA:\n", 1)[1])
            paragraphs = [{"text": fact["text"], "claim_ids": [fact["claim_id"]]}
                          for fact in facts]
            if draft_calls == 1:
                paragraphs[0]["text"] += " This proves the population is high risk."
            return schema(paragraphs=paragraphs)
        if schema.__name__ == "ReportVerification":
            verification_calls += 1
            payload = json.loads(prompt.split("DATA:\n", 1)[1])
            return schema(checks=[{
                "paragraph_index": index,
                "supported": verification_calls > 1,
                "reason": "grounded" if verification_calls > 1 else "unsupported extrapolation",
            } for index, _item in enumerate(payload["paragraphs"])])
        if schema.__name__ == "ReportSectionRepair":
            repair_calls += 1
            payload = json.loads(prompt.split("DATA:\n", 1)[1])
            facts = payload["facts"]
            return schema(
                heading="Cycle life",
                paragraphs=[{"text": fact["text"], "claim_ids": [fact["claim_id"]]}
                            for fact in facts],
            )
        return model_reply(prompt, schema)

    result = await ResearchPipeline(
        config(tmp_path), llm=MockLLMClient(structured_response=reply)
    ).run()

    assert result.verdict == "STOP_SUCCESS"
    assert result.report_result is not None
    assert repair_calls == 1
    assert verification_calls == 2


@pytest.mark.asyncio
async def test_identical_reprints_across_rounds_do_not_satisfy_independence(tmp_path, external_io, monkeypatch):
    async def reprint(self, url):
        return FetchResult(url, QUOTE + "\n\n" + "Experimental methods and limitations. " * 8, ok=True)
    monkeypatch.setattr(Fetcher, "fetch", reprint)
    cfg = config(tmp_path, minimum=2)
    cfg.nine_loop.max_rounds = 1
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_BUDGET" and result.report_result is None


@pytest.mark.asyncio
async def test_model_finding_cannot_downgrade_program_coverage(tmp_path, external_io):
    def collision(prompt, schema):
        if schema.__name__ == "ResearchInspection":
            return schema(findings=[{"finding_id": "coverage:topic:Cycle life", "priority": "low",
                                     "description": "override", "query": "battery", "claim_ids": []}])
        return model_reply(prompt, schema)
    cfg = config(tmp_path, minimum=2)
    cfg.nine_loop.max_rounds = 0
    cfg.nine_loop.max_total_findings = 1
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=collision)).run()
    assert result.verdict == "STOP_BUDGET" and result.report_result is None


@pytest.mark.asyncio
async def test_same_pipeline_instance_reuse_does_not_reuse_other_run_state(tmp_path, external_io):
    cfg = config(tmp_path)
    cfg.resume = False
    pipe = ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply))
    first = await pipe.run()
    cfg.input_dir = tmp_path / "absent"
    pipe.config.input_dir = cfg.input_dir
    second = await pipe.run()
    summary = json.loads(second.run_summary_path.read_text())
    assert second.verdict == "STOP_ERROR" and second.failed_stage == "collect"
    assert summary["run_id"] is None
    assert summary["output_dir"] is None
    assert (first.report_result.report_path).exists()


@pytest.mark.asyncio
async def test_partial_attempt_files_never_enter_retried_extract(tmp_path):
    """Interrupted extraction must retry from source bodies, not stale derived JSON."""
    raw = tmp_path / "input"
    raw.mkdir()
    (raw / "01.md").write_text("<!-- source: https://source.test/a -->\n\n" + QUOTE)
    cfg = config(tmp_path / "out")
    cfg.input_dir = raw
    cfg.llm_retry_backoff_sec = 0
    model = MockLLMClient(structured_response=model_reply)
    first = await ResearchPipeline(cfg, llm=model).run()
    report = first.report_result.report_path
    report.unlink()
    second = await ResearchPipeline(cfg, llm=model).run()
    assert second.verdict == "STOP_ERROR" and second.report_result is None
    assert not report.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_evidence", [False, True])
async def test_model_cannot_drop_prior_finding_without_resolving_evidence(
    tmp_path, external_io, new_evidence,
):
    inspections = 0

    def reply(prompt, schema):
        nonlocal inspections
        if schema.__name__ == "ResearchInspection":
            inspections += 1
            if inspections == 1:
                return schema(findings=[{
                    "finding_id": "limitation", "priority": "high",
                    "description": "Independent limitation evidence is absent",
                    "query": "battery independent evidence" if new_evidence else "battery",
                }])
            return schema(findings=[])
        return model_reply(prompt, schema)

    cfg = config(tmp_path, minimum=1)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 1
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=reply)).run()

    assert result.verdict == "STOP_BUDGET"
    assert result.report_result is None
    latest = json.loads(next(result.topic_dir.glob("artifacts/*/inspect-1.json")).read_text())
    assert any(f["finding_id"] == "model:limitation" for f in latest["result"]["findings"])


@pytest.mark.asyncio
async def test_unverified_report_heading_blocks_publication(tmp_path, external_io):
    heading = "Every battery retains 100 percent capacity forever"

    def reply(prompt, schema):
        if schema.__name__ == "ReportOutline":
            data = json.loads(prompt.split("DATA:\n", 1)[1])
            return schema(sections=[{
                "heading": heading, "claim_ids": [f["claim_id"] for f in data["facts"]],
            }])
        if schema.__name__ == "ReportVerification":
            data = json.loads(prompt.split("DATA:\n", 1)[1])
            return schema(checks=[{
                "paragraph_index": index, "supported": item["text"] != heading,
                "reason": "Heading exceeds the measured source evidence",
            } for index, item in enumerate(data["paragraphs"])])
        return model_reply(prompt, schema)

    result = await ResearchPipeline(
        config(tmp_path), llm=MockLLMClient(structured_response=reply),
    ).run()

    assert result.verdict == "STOP_ERROR" and result.failed_stage == "report"
    assert result.report_result is None


@pytest.mark.asyncio
async def test_wall_timeout_records_stopping_stage_and_metric(tmp_path):
    cfg = config(tmp_path)
    cfg.nine_loop.wall_s_max = 1e-12
    result = await ResearchPipeline(cfg, llm=MockLLMClient()).run()

    assert result.verdict == "STOP_BUDGET" and result.failed_stage == "collect"
    assert result.stage_metrics[-1].stage == "collect"
    assert result.stage_metrics[-1].status == "failed"
    assert json.loads(result.run_summary_path.read_text())["failed_stage"] == "collect"


@pytest.mark.asyncio
async def test_prior_model_finding_can_resolve_only_with_new_claim_and_reason(tmp_path, external_io):
    inspections = 0

    def reply(prompt, schema):
        nonlocal inspections
        if schema.__name__ == "ResearchInspection":
            inspections += 1
            if inspections == 1:
                return schema(findings=[{
                    "finding_id": "independent", "priority": "high",
                    "description": "Independent test missing",
                    "query": "battery independent evidence",
                }])
            facts = json.loads(prompt.split("DATA:\n", 1)[1])
            claim_id = next(f["claim_id"] for f in facts if f["text"] == SECOND)
            return schema(resolutions=[{
                "finding_id": "model:independent", "claim_ids": [claim_id],
                "reason": "The newly retrieved independent experiment supplies the missing test.",
            }])
        return model_reply(prompt, schema)

    cfg = config(tmp_path, minimum=1)
    cfg.collector.strategies.enabled = False
    cfg.nine_loop.max_rounds = 1
    model = MockLLMClient(structured_response=reply)
    first = await ResearchPipeline(cfg, llm=model).run()
    assert first.verdict == "STOP_SUCCESS"
    inspection = json.loads(next(first.topic_dir.glob("artifacts/*/inspect-1.json")).read_text())
    assert inspection["result"]["resolutions"][0]["claim_ids"]
    resumed_model = MockLLMClient(structured_response=lambda *a: pytest.fail("resume used model"))
    resumed = await ResearchPipeline(cfg, llm=resumed_model).run()
    assert resumed.verdict == "STOP_SUCCESS" and not resumed_model.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["snippet", "discovery"])
async def test_discovery_metadata_cannot_enter_formal_fact_ledger(tmp_path, kind):
    raw = tmp_path / "input"
    raw.mkdir()
    (raw / "01.md").write_text(
        "<!-- source: https://evidence.test/a -->\n"
        f"<!-- evidence_kind: {kind} -->\n\n" + QUOTE
    )
    cfg = config(tmp_path / "output")
    cfg.input_dir = raw
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_ERROR" and result.failed_stage == "collect"
    assert result.report_result is None
