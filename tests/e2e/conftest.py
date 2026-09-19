"""Global fixtures and test harness for opaque-box E2E testing.

Provides isolated environments, CLI runner helpers, mock search/LLM responses,
Protocol v1 envelopes, and state verification utilities.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest


# --------------------------------------------------------------------------- #
# CLI Subprocess Runner
# --------------------------------------------------------------------------- #
@pytest.fixture
def run_cli() -> Callable[..., subprocess.CompletedProcess]:
    """Execute research-tool CLI via subprocess in an opaque-box manner."""

    def _runner(
        args: List[str],
        cwd: Path | str | None = None,
        env: Dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess:
        current_env = os.environ.copy()
        current_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        if env:
            current_env.update(env)

        cmd = [sys.executable, "-m", "research_tool.presentation.cli"] + args
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=current_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    return _runner


# --------------------------------------------------------------------------- #
# Mock Adapter Client for 9-Stage Chain
# --------------------------------------------------------------------------- #
class MockAdapterClient:
    """In-process mock for AdapterClient that returns valid identities."""

    def __init__(self, deadline_s: float = 30.0):
        self.deadline_s = deadline_s

    def encode_requests(self, requests: list[dict[str, Any]]) -> bytes:
        return b"\n".join(
            json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            for r in requests
        ) + b"\n"

    def run_raw(self, payload: bytes) -> Any:
        from research_tool.nine_loop.rt_identity_adapter import BatchResult

        records = [
            {
                "ok": True,
                "id": "req-01",
                "identities": [
                    {
                        "input_url": "https://arxiv.org/abs/2301.00001",
                        "canonical_locator": "exid01.v1:arxiv:2301.00001",
                        "source_id": "src_001",
                        "decision": "retain",
                        "content_sha256": "a" * 64,
                    }
                ],
            }
        ]
        stdout_bytes = json.dumps(records[0]).encode("utf-8") + b"\n"
        return BatchResult(records=records, exit_code=0, stdout_bytes=stdout_bytes, stderr_bytes=b"")


@pytest.fixture
def mock_adapter_client() -> MockAdapterClient:
    return MockAdapterClient()

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create an isolated, dedicated research directory for an E2E test."""
    work_dir = tmp_path / "workspace"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# --------------------------------------------------------------------------- #
# Protocol v1 Envelope Fixtures
# --------------------------------------------------------------------------- #
def make_envelope(
    stage: str,
    run_id: str = "run-test-001",
    request_id: str = "req-001",
    result: Any = None,
    error: Any = None,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    """Generate a valid Protocol v1 stage envelope."""
    payload = {
        "v": 1,
        "run_id": run_id,
        "stage": stage,
        "request_id": request_id,
        "idempotency_key": idempotency_key or hashlib.sha256(f"{run_id}:{stage}".encode()).hexdigest(),
        "budget_lease": {
            "lease_id": f"lease-{stage}-001",
            "tokens_max": 100000,
            "cost_max": 5.0,
            "wall_s_max": 300.0,
            "search_calls_max": 20,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
        "result": result if result is not None else {"status": "ok", "items": []},
        "error": error,
    }
    return payload


@pytest.fixture
def sample_collect_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="collect",
        result={
            "sources": [
                {
                    "url": "https://arxiv.org/abs/2301.00001",
                    "title": "Quantum Foundations in Machine Learning",
                    "content_hash": "sha256:abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abc",
                    "source_engine": "arxiv",
                    "fetched_at": "2026-09-17T14:00:00Z",
                },
                {
                    "url": "https://thecvf.com/papers/2026/paper1.pdf",
                    "title": "Autonomous Vision Navigation",
                    "content_hash": "sha256:def1234567890abcdef1234567890abcdef1234567890abcdef1234567890def",
                    "source_engine": "cvpr",
                    "fetched_at": "2026-09-17T14:00:00Z",
                },
            ]
        },
    )


@pytest.fixture
def sample_clean_envelope(sample_collect_envelope: Dict[str, Any]) -> Dict[str, Any]:
    return make_envelope(
        stage="clean",
        result={
            "cleaned_documents": [
                {
                    "url": "https://arxiv.org/abs/2301.00001",
                    "canonical_locator": "exid01.v1:arxiv:2301.00001",
                    "title": "Quantum Foundations in Machine Learning",
                    "clean_text": "Quantum neural networks offer exponential speedup in state estimation.",
                    "content_sha256": "abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abc",
                }
            ]
        },
    )


@pytest.fixture
def sample_extract_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="extract",
        result={
            "entities": [
                {"id": "ent-1", "name": "Quantum Neural Network", "type": "method"},
                {"id": "ent-2", "name": "State Estimation", "type": "task"},
            ],
            "facts": [
                {
                    "fact_id": "f-01",
                    "subject": "Quantum Neural Network",
                    "predicate": "accelerates",
                    "object": "State Estimation",
                    "source_id": "src_123",
                    "confidence": 0.95,
                }
            ],
        },
    )


@pytest.fixture
def sample_knowledge_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="knowledge",
        result={
            "nodes": [
                {
                    "id": "node-1",
                    "title": "Quantum Machine Learning Overview",
                    "claims": [
                        {
                            "claim_id": "c-01",
                            "statement": "Quantum neural networks provide speedup for state estimation.",
                            "citations": ["src_123"],
                        }
                    ],
                }
            ],
            "graph_digest": "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
            "version": 1,
        },
    )


@pytest.fixture
def sample_inspect_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="inspect",
        result={
            "findings": [
                {
                    "finding_id": "gap-01",
                    "severity": "medium",
                    "type": "structural_gap",
                    "description": "Missing empirical benchmark comparison for state estimation runtime.",
                    "suggested_query": "Quantum neural network state estimation benchmark runtime",
                }
            ],
            "gap_count": 1,
            "contradiction_count": 0,
        },
    )


@pytest.fixture
def sample_qgate_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="qgate",
        result={
            "decision": "CONTINUE",
            "threshold_eval": {
                "high_findings": 0,
                "total_findings": 1,
                "passed": False,
            },
            "remaining_budget": {"search_calls": 18, "tokens": 95000},
        },
    )


@pytest.fixture
def sample_report_envelope() -> Dict[str, Any]:
    return make_envelope(
        stage="report",
        result={
            "report_path": "report.md",
            "citation_coverage": 1.0,
            "verified_claims_count": 5,
            "dropped_claims": [],
            "status": "published",
        },
    )


# --------------------------------------------------------------------------- #
# Downstream Artifact Generator
# --------------------------------------------------------------------------- #
@pytest.fixture
def generate_valid_research_output() -> Callable[[Path], Dict[str, Path]]:
    """Helper to populate a directory with authentic 9-stage deliverables."""

    def _populate(target_dir: Path) -> Dict[str, Path]:
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. artifacts/
        artifacts_dir = target_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        for stage in ("collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"):
            env = make_envelope(stage=stage)
            data = json.dumps(env, sort_keys=True, ensure_ascii=False).encode("utf-8")
            (artifacts_dir / f"{stage}.json").write_bytes(data)

        # 2. state.json
        state = {
            "version": 1,
            "input_idempotency_key": hashlib.sha256(b"test-input").hexdigest(),
            "stages": {
                stage: hashlib.sha256((artifacts_dir / f"{stage}.json").read_bytes()).hexdigest()
                for stage in ("collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report")
            },
            "done": ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"],
        }
        (target_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

        # 3. tree/00-主表.md
        tree_dir = target_dir / "tree"
        tree_dir.mkdir(exist_ok=True)
        (tree_dir / "00-主表.md").write_text(
            "# 知识网络大纲\n\n- [[N01-量子机器学习|量子机器学习概述]]\n- [[N02-状态估计|状态估计]]\n",
            encoding="utf-8",
        )
        (tree_dir / "N01-量子机器学习.md").write_text(
            "<!-- source: https://arxiv.org/abs/2301.00001 -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n# 量子机器学习\n内容详述。",
            encoding="utf-8",
        )

        # 4. report.md
        (target_dir / "report.md").write_text(
            "<!-- source: https://arxiv.org/abs/2301.00001 -->\n<!-- fetched: 2026-09-17T14:00:00Z -->\n# 调研报告：量子机器学习\n\n## 结论\n量子神经网络具有理论加速优势 [^1]。\n\n[^1]: https://arxiv.org/abs/2301.00001\n",
            encoding="utf-8",
        )

        # 5. sources.json
        sources = [
            {
                "url": "https://arxiv.org/abs/2301.00001",
                "title": "Quantum Foundations in Machine Learning",
                "fetchedAt": "2026-09-17T14:00:00Z",
                "content_hash": "sha256:abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abc",
            }
        ]
        (target_dir / "sources.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")

        # 6. run-summary.json
        summary = {
            "version": 1,
            "pipeline_complete": True,
            "stages_completed": ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"],
            "stages_skipped": [],
            "citation_coverage": 1.0,
            "elapsed_sec": 12.34,
        }
        (target_dir / "run-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

        return {
            "root": target_dir,
            "report": target_dir / "report.md",
            "tree_index": tree_dir / "00-主表.md",
            "sources": target_dir / "sources.json",
            "summary": target_dir / "run-summary.json",
            "state": target_dir / "state.json",
        }

    return _populate
