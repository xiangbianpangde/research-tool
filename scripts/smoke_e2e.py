"""Offline full-pipeline smoke, including a targeted research round and resume.

Runs real collection storage, cleaner, extractor, graph, state and reporting.
The only fake is the search/fetch/model I/O boundary. This is a wiring check,
not a research-quality measurement or a reproduction of the historical score.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RESEARCH_DISABLE_DOTENV", "1")

from research_tool.application.pipeline import ResearchPipeline  # noqa: E402
from research_tool.domain.models import (  # noqa: E402
    CleanerConfig, CollectorConfig, OrganizerConfig, PipelineConfig,
)
from research_tool.infrastructure.llm import MockLLMClient  # noqa: E402
from research_tool.infrastructure.search.base import SearchHit  # noqa: E402
from research_tool.infrastructure.stages import collector as collector_module  # noqa: E402
from research_tool.infrastructure.stages.fetcher import FetchResult, Fetcher  # noqa: E402

INITIAL = "The measured battery retained 90 percent capacity after 1000 cycles."
TARGETED = "An independent test observed 88 percent retention after 1000 cycles."


class LocalSearch:
    async def search(self, query, *args, **kwargs):
        suffix = "b" if "independent evidence" in query else "a"
        return [SearchHit(url=f"https://evidence.test/{suffix}", title="Battery experiment",
                          source_engine="web")]


async def local_fetch(self, url: str) -> FetchResult:
    text = TARGETED if url.endswith("/b") else INITIAL
    return FetchResult(url=url, markdown=text, ok=True)


def local_model(prompt: str, schema):
    name = schema.__name__
    if name == "_ChunkResult":
        quote = TARGETED if TARGETED in prompt else INITIAL
        return schema(claims=[{"text": quote, "quote": quote, "topic": "Cycle life"}])
    if name == "ResearchInspection":
        return schema(findings=[])
    if name == "ReportOutline":
        data = json.loads(prompt.split("DATA:\n", 1)[1])
        return schema(sections=[{"heading": "Cycle life",
                                 "claim_ids": [f["claim_id"] for f in data["facts"]]}])
    if name == "ReportSectionDraft":
        facts = json.loads(prompt.split("DATA:\n", 1)[1])
        return schema(paragraphs=[{"text": f["text"], "claim_ids": [f["claim_id"]]}
                                  for f in facts])
    if name == "ReportVerification":
        data = json.loads(prompt.split("DATA:\n", 1)[1])
        facts = {fact["claim_id"]: fact for fact in data["facts"]}
        return schema(checks=[{
            "paragraph_index": index,
            "supported": (paragraph["text"] == "Cycle life"
                          if paragraph.get("kind") == "heading" else
                          all(paragraph["text"] == facts[cid]["text"]
                              for cid in paragraph["claim_ids"])),
            "reason": "Fixture title describes cycle life; body must equal its source quote",
        } for index, paragraph in enumerate(data["paragraphs"])])
    if name == "_Scores":
        return schema(scores=[1.0])
    return schema()


async def main(output: Path) -> int:
    original_backend, original_fetch = collector_module.get_backend, Fetcher.fetch
    collector_module.get_backend = lambda *args: LocalSearch()
    Fetcher.fetch = local_fetch
    try:
        cfg = PipelineConfig(
            topic="Nine-stage battery smoke", work_dir=output,
            collector=CollectorConfig(search_engines=["web"], search_cache=False,
                                      min_doc_chars=20, search_relevance_min_overlap=0,
                                      save_originals=False, strategies={"enabled": False}),
            cleaner=CleanerConfig(min_content_length=20, dedup_similarity=1.0),
            organizer=OrganizerConfig(min_nodes=1, max_nodes=4, min_evidence_per_node=2),
        )
        model = MockLLMClient(structured_response=local_model)
        first = await ResearchPipeline(cfg, llm=model).run()
        if first.verdict != "STOP_SUCCESS" or not first.report_result:
            raise RuntimeError(first.model_dump(mode="json"))
        resumed_model = MockLLMClient(structured_response=lambda *args: _unexpected_model())
        resumed = await ResearchPipeline(cfg, llm=resumed_model).run()
        if resumed.verdict != "STOP_SUCCESS" or resumed_model.calls:
            raise RuntimeError("Resume did not reuse validated outputs")
        summary = json.loads(first.run_summary_path.read_text())
        receipt = {"status": "PASS", "mode": "offline_external_io_fixtures",
                   "rounds": summary["rounds"], "stages": summary["stages"],
                   "run_id": summary["run_id"], "report": str(first.report_result.report_path),
                   "report_sha256": hashlib.sha256(
                       first.report_result.report_path.read_bytes()).hexdigest(),
                   "summary": str(first.run_summary_path), "resume_model_calls": 0}
        (output / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    finally:
        collector_module.get_backend, Fetcher.fetch = original_backend, original_fetch


def _unexpected_model():
    raise AssertionError("A resumed pipeline called the model")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(tempfile.mkdtemp(prefix="research-nine-smoke-")))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(main(args.output)))
