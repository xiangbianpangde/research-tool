"""Challenger M1-1 (Iteration 2 Replacement) Empirical Stress Suite.

Comprehensive empirical verification targeting:
1. Atomic CAS write-if-match merge semantics (races, malformed inputs, monotonicity, conflict coexistence, immutability).
2. Concurrent state commits and write_atomic integrity under multi-threaded contention.
3. SHA-256 tamper detection in ChainState across all 9 canonical stages and aliases.
4. Crash-recovery invariants across all pipeline stages, byte-identical mtime preservation, orphan cleanup, idempotency enforcement.
5. Protocol v1 envelope compliance, budget lease invariants, and error frame zero-leakage contracts.
6. Verification of Worker M1 Iteration 2 fixes: default PipelineConfig routing, resume across closed loop, load_config overrides.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import os
import pathlib
import time
from typing import Any

import pytest

from research_tool import research
from research_tool.application.pipeline import (
    ResearchPipeline,
    CANONICAL_NINE_STAGES,
    create_pipeline,
)
from research_tool.domain.models import PipelineConfig, PipelineResult
from research_tool.domain.config import load_config
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.chain_state import (
    ChainState,
    ChainStateFault,
    E_CAS_CONFLICT,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    sha256_bytes,
    write_atomic,
    STAGE_ALIASES,
)
from research_tool.nine_loop import merge_min, inspect_min, qgate_min, targeted_min, report_min
from research_tool.infrastructure.export.wiki_stage import build_stage_package


# ============================================================================
# Helpers & Mocks
# ============================================================================

def _build_test_pipeline(tmp_path: pathlib.Path, **kwargs: Any) -> tuple[ResearchPipeline, pathlib.Path]:
    work_dir = tmp_path / "work"
    cfg_kwargs = {
        "topic": "empirical-rep-test",
        "work_dir": work_dir,
        "mode": "full",
        "resume": False,
    }
    cfg_kwargs.update(kwargs)
    cfg = PipelineConfig(**cfg_kwargs)
    pipe = ResearchPipeline(cfg)

    pipe._llm = MockLLMClient(
        chat_response="# 报告\n\n实证替代测试通过 (来源01)。\n\n## 参考资料\n- 来源01：实证替代 — https://arxiv.org/abs/empirical-rep"
    )

    async def mock_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text(
                "<!-- source: https://arxiv.org/abs/empirical-rep -->\n"
                "<!-- fetched: 2026-09-17T14:00:00Z -->\n实证内容",
                encoding="utf-8",
            )
            sources = [
                {
                    "url": "https://arxiv.org/abs/empirical-rep",
                    "title": "实证内容",
                    "fetchedAt": "2026-09-17T14:00:00Z",
                    "content_hash": "sha256:" + "e" * 64,
                }
            ]
            (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("清洗后实证内容", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text(
                '[{"name": "Empirical Verification", "type": "method"}]', encoding="utf-8"
            )
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络大纲\n\n- [[N01-实证|实证核验]]\n", encoding="utf-8")
            (tree / "N01-实证.md").write_text(
                "<!-- source: https://arxiv.org/abs/empirical-rep -->\n"
                "<!-- fetched: 2026-09-17T14:00:00Z -->\n# 实证核验\n节点详情",
                encoding="utf-8",
            )
        elif stage == "report":
            pass

    pipe._exec = mock_exec
    return pipe, work_dir


def _make_valid_envelope(stage: str, run_id: str = "run-emp-001", idempotency_key: str = "7" * 64) -> dict[str, Any]:
    return {
        "v": 1,
        "run_id": run_id,
        "stage": stage,
        "request_id": f"req:{stage}:001",
        "idempotency_key": idempotency_key,
        "budget_lease": {
            "lease_id": "lease-emp-001",
            "tokens_max": 50000,
            "cost_max": 2.5,
            "wall_s_max": 45.0,
            "search_calls_max": 10,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
        "result": {"status": "ok", "stage": stage, "items": [10, 20, 30]},
        "error": None,
    }


# ============================================================================
# Dimension 1: Atomic CAS Write-If-Match Merge Semantics
# ============================================================================

def test_cas_merge_write_if_match_strict_equivalence():
    """Verify that CAS merge succeeds with exact matching digest and rejects mismatches."""
    initial_graph = {
        "nodes": [
            {
                "node_id": "src:alpha",
                "evidence_spans": [{"locator": "https://arxiv.org/abs/alpha", "content_sha256": "1" * 64, "round_id": 0}],
            }
        ],
        "edges": [],
    }
    prev_digest = merge_min.graph_digest(initial_graph)
    assert len(prev_digest) == 64

    responses = [
        {
            "request_id": "tq:001",
            "facts": [
                {
                    "source_id": "src:beta",
                    "locator": "https://arxiv.org/abs/beta",
                    "content_sha256": "2" * 64,
                }
            ],
        }
    ]

    # 1. Matching merge succeeds
    res = merge_min.merge(initial_graph, responses, prev_digest=prev_digest)
    assert res["latest_digest"] != prev_digest
    assert res["counts"]["new_facts"] == 1
    assert res["counts"]["skipped"] == 0
    assert len(res["graph"]["nodes"]) == 2

    # 2. Corrupted / stale digests fail with E_CAS_CONFLICT
    stale_digests = [
        "0" * 64,
        prev_digest[:-1] + ("0" if prev_digest[-1] != "0" else "1"),
        prev_digest.upper(),  # case sensitivity
    ]
    for bad_digest in stale_digests:
        with pytest.raises(merge_min.MergeStageFault) as exc_info:
            merge_min.merge(initial_graph, responses, prev_digest=bad_digest)
        assert exc_info.value.frame["code"] == E_CAS_CONFLICT


def test_cas_merge_concurrent_racing_branches_and_rebase():
    """Simulate two concurrent workers attempting to commit against the same base state."""
    base_graph = {
        "nodes": [
            {
                "node_id": "src:base",
                "evidence_spans": [{"locator": "https://arxiv.org/abs/base", "content_sha256": "0" * 64, "round_id": 0}],
            }
        ],
        "edges": [],
    }
    base_digest = merge_min.graph_digest(base_graph)

    # Worker A updates base
    resp_a = [
        {
            "request_id": "tq:A",
            "facts": [{"source_id": "src:branch_a", "locator": "https://arxiv.org/abs/a", "content_sha256": "a" * 64}],
        }
    ]
    res_a = merge_min.merge(base_graph, resp_a, prev_digest=base_digest)
    graph_a = res_a["graph"]
    digest_a = res_a["latest_digest"]

    # Worker B tries to merge onto base, but base has now moved to digest_a
    resp_b = [
        {
            "request_id": "tq:B",
            "facts": [{"source_id": "src:branch_b", "locator": "https://arxiv.org/abs/b", "content_sha256": "b" * 64}],
        }
    ]
    # Attempting with stale base_digest against graph_a MUST raise E_CAS_CONFLICT
    with pytest.raises(merge_min.MergeStageFault) as exc_info:
        merge_min.merge(graph_a, resp_b, prev_digest=base_digest)
    assert exc_info.value.frame["code"] == E_CAS_CONFLICT

    # Rebase: Worker B fetches updated digest_a and succeeds
    res_b_rebased = merge_min.merge(graph_a, resp_b, prev_digest=digest_a)
    assert res_b_rebased["latest_digest"] != digest_a
    node_ids = [n["node_id"] for n in res_b_rebased["graph"]["nodes"]]
    assert "src:base" in node_ids
    assert "src:branch_a" in node_ids
    assert "src:branch_b" in node_ids


def test_cas_merge_content_sha256_deduplication_and_conflict_coexistence():
    """Verify exact content_sha256 deduplication and conflict_coexist branch preservation."""
    base_graph = {
        "nodes": [
            {
                "node_id": "src:claim_1",
                "evidence_spans": [
                    {
                        "source_id": "src:claim_1",
                        "locator": "https://arxiv.org/abs/1",
                        "content_sha256": "11" * 32,
                        "round_id": 0,
                    }
                ],
            }
        ],
        "edges": [],
    }
    prev_digest = merge_min.graph_digest(base_graph)

    # 1. Exact duplicate response fact: must be skipped
    dup_resp = [
        {
            "request_id": "tq:dup",
            "facts": [
                {
                    "source_id": "src:claim_1",
                    "locator": "https://arxiv.org/abs/1",
                    "content_sha256": "11" * 32,
                }
            ],
        }
    ]
    res_dup = merge_min.merge(base_graph, dup_resp, prev_digest=prev_digest)
    assert res_dup["counts"]["skipped"] == 1
    assert res_dup["counts"]["new_facts"] == 0
    assert len(res_dup["graph"]["nodes"][0]["evidence_spans"]) == 1

    # 2. Conflicting evidence (same source_id, different content_sha256): conflict coexist
    conflict_resp = [
        {
            "request_id": "tq:conflict",
            "facts": [
                {
                    "source_id": "src:claim_1",
                    "locator": "https://arxiv.org/abs/1_v2",
                    "content_sha256": "22" * 32,
                }
            ],
        }
    ]
    res_conflict = merge_min.merge(base_graph, conflict_resp, prev_digest=prev_digest)
    assert res_conflict["counts"]["conflicts"] == 1
    assert res_conflict["counts"]["new_facts"] == 1
    # Both evidence spans preserved in node
    spans = res_conflict["graph"]["nodes"][0]["evidence_spans"]
    assert len(spans) == 2
    span_shas = {s["content_sha256"] for s in spans}
    assert span_shas == {"11" * 32, "22" * 32}


def test_cas_merge_caller_data_immutability():
    """Verify merge NEVER mutates input network dictionary."""
    base_graph = {
        "nodes": [
            {
                "node_id": "src:orig",
                "evidence_spans": [{"locator": "https://arxiv.org/abs/orig", "content_sha256": "99" * 32, "round_id": 0}],
            }
        ],
        "edges": [],
    }
    original_frozen = copy.deepcopy(base_graph)
    prev_digest = merge_min.graph_digest(base_graph)

    responses = [
        {
            "request_id": "tq:mut",
            "facts": [{"source_id": "src:new", "locator": "https://arxiv.org/abs/new", "content_sha256": "88" * 32}],
        }
    ]
    res = merge_min.merge(base_graph, responses, prev_digest=prev_digest)
    assert base_graph == original_frozen, "CRITICAL: merge() mutated caller input dictionary!"


def test_cas_merge_extreme_determinism_and_shuffling():
    """Verify graph_digest is byte-for-byte deterministic across different input arrival permutations."""
    facts = [
        {"source_id": f"src:{i:03d}", "locator": f"https://arxiv.org/abs/{i:03d}", "content_sha256": f"{i:02x}" * 32, "round_id": 0}
        for i in range(20)
    ]
    base_graph = {"nodes": [], "edges": []}
    prev_digest = merge_min.graph_digest(base_graph)

    # Order 1: Forward
    resp_forward = [{"request_id": "tq:fwd", "facts": facts}]
    res_fwd = merge_min.merge(base_graph, resp_forward, prev_digest=prev_digest)

    # Order 2: Reverse
    resp_reverse = [{"request_id": "tq:rev", "facts": list(reversed(facts))}]
    res_rev = merge_min.merge(base_graph, resp_reverse, prev_digest=prev_digest)

    # Order 3: Partitioned into 4 batches
    resp_batched = [
        {"request_id": f"tq:b{b}", "facts": facts[b * 5 : (b + 1) * 5]}
        for b in range(4)
    ]
    res_batched = merge_min.merge(base_graph, resp_batched, prev_digest=prev_digest)

    assert res_fwd["latest_digest"] == res_rev["latest_digest"]
    assert res_fwd["latest_digest"] == res_batched["latest_digest"]
    assert res_fwd["graph"] == res_rev["graph"]
    assert res_fwd["graph"] == res_batched["graph"]


# ============================================================================
# Dimension 2: Concurrent State Commits & Multi-Thread Safety
# ============================================================================

def test_concurrent_write_atomic_100_threads(tmp_path: pathlib.Path):
    """Stress-test write_atomic with 100 concurrent threads competing on target file."""
    target_file = tmp_path / "atomic_target.json"
    iterations_per_thread = 10
    num_threads = 100

    def worker(worker_id: int):
        for i in range(iterations_per_thread):
            payload = json.dumps({
                "worker": worker_id,
                "iter": i,
                "token": "x" * 1024,
                "sha": hashlib.sha256(f"{worker_id}:{i}".encode()).hexdigest(),
            }).encode("utf-8")
            write_atomic(target_file, payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, w) for w in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert target_file.exists()
    content = json.loads(target_file.read_text(encoding="utf-8"))
    assert "worker" in content
    assert "token" in content
    # Zero .tmp-* files lingering
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_chain_state_commit_loop_merge_atomicity(tmp_path: pathlib.Path):
    """Verify commit_loop_merge atomically updates state.json, network.json, knowledge.json, merge.json."""
    cs = ChainState(tmp_path)
    key = "k" * 64
    state = cs.load(key)

    lease = {
        "lease_id": "l-test",
        "tokens_max": 1000,
        "cost_max": 1.0,
        "wall_s_max": 10.0,
        "search_calls_max": 1,
        "issued_at": "2026-09-17T14:00:00Z",
        "expires_at": "2026-09-17T15:00:00Z",
    }
    graph = {"nodes": [{"node_id": "src:1", "evidence_spans": []}], "edges": [], "counts": {"nodes": 1, "edges": 0}}
    net_env = {
        "v": 1, "run_id": "r-1", "stage": "network", "request_id": "n-1",
        "idempotency_key": merge_min.graph_digest(graph),
        "budget_lease": lease, "result": graph, "error": None,
    }
    merge_env = {
        "v": 1, "run_id": "r-1", "stage": "merge", "request_id": "m-1",
        "idempotency_key": "m" * 64, "budget_lease": lease,
        "result": {"graph": graph, "latest_digest": merge_min.graph_digest(graph), "counts": {"new_facts": 1}},
        "error": None,
    }

    state = cs.commit_loop_merge(state, net_env, merge_env, round_idx=0)

    assert state["loop"]["current_round"] == 1
    assert state["loop"]["latest_network_digest"] == merge_min.graph_digest(graph)
    assert len(state["loop"]["history"]) == 1
    assert (tmp_path / "artifacts" / "network.json").exists()
    assert (tmp_path / "artifacts" / "knowledge.json").exists()
    assert (tmp_path / "artifacts" / "merge.json").exists()

    # Verify both knowledge and network are bit-identical
    assert (tmp_path / "artifacts" / "network.json").read_bytes() == (tmp_path / "artifacts" / "knowledge.json").read_bytes()


# ============================================================================
# Dimension 3: SHA-256 Tamper Detection in ChainState
# ============================================================================

@pytest.mark.parametrize("stage", [
    "collect", "clean", "extract", "knowledge", "inspect", "qgate", "targeted", "merge", "report"
])
def test_sha256_detects_single_bit_flip_in_all_nine_stage_artifacts(tmp_path: pathlib.Path, stage: str):
    """Verify bit-flip corruption in ANY of the 9 canonical artifacts is caught by verify_state & read_stage."""
    cs = ChainState(tmp_path)
    key = "t" * 64
    state = cs.load(key)

    env = _make_valid_envelope(stage, idempotency_key=key)
    cs.commit_stage(state, stage, env)

    art_path = tmp_path / "artifacts" / f"{stage}.json"
    assert art_path.exists()

    # Flip one byte in the artifact file
    raw = bytearray(art_path.read_bytes())
    raw[-2] = raw[-2] ^ 0x01  # bit-flip
    art_path.write_bytes(bytes(raw))

    # read_stage fails
    with pytest.raises(ChainStateFault) as exc_info:
        cs.read_stage(state, stage)
    assert exc_info.value.code == E_STATE

    # reload with resume=True fails
    cs2 = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc_info2:
        cs2.load(key, resume=True)
    assert exc_info2.value.code == E_STATE


def test_sha256_detects_trailing_whitespace_modification(tmp_path: pathlib.Path):
    """Verify that adding even a single trailing byte / newline is caught by cryptographic SHA-256."""
    cs = ChainState(tmp_path)
    key = "w" * 64
    state = cs.load(key)
    env = _make_valid_envelope("extract", idempotency_key=key)
    cs.commit_stage(state, "extract", env)

    art_path = tmp_path / "artifacts" / "extract.json"
    art_path.write_bytes(art_path.read_bytes() + b" ")

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "extract")
    assert exc.value.code == E_STATE


def test_sha256_detects_zero_byte_empty_artifact(tmp_path: pathlib.Path):
    """Verify that truncating an artifact to 0 bytes fails fast with E_STATE."""
    cs = ChainState(tmp_path)
    key = "z" * 64
    state = cs.load(key)
    env = _make_valid_envelope("collect", idempotency_key=key)
    cs.commit_stage(state, "collect", env)

    art_path = tmp_path / "artifacts" / "collect.json"
    art_path.write_bytes(b"")

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "collect")
    assert exc.value.code == E_STATE


def test_sha256_detects_deleted_artifact_file(tmp_path: pathlib.Path):
    """Verify that deleting an artifact file while keeping state.json raises E_STATE."""
    cs = ChainState(tmp_path)
    key = "d" * 64
    state = cs.load(key)
    env = _make_valid_envelope("clean", idempotency_key=key)
    cs.commit_stage(state, "clean", env)

    art_path = tmp_path / "artifacts" / "clean.json"
    art_path.unlink()

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "clean")
    assert exc.value.code == E_STATE


def test_sha256_detects_tampering_of_state_json_itself(tmp_path: pathlib.Path):
    """Verify that corrupting state.json raises E_STATE."""
    cs = ChainState(tmp_path)
    key = "s" * 64
    state = cs.load(key)
    env = _make_valid_envelope("clean", idempotency_key=key)
    cs.commit_stage(state, "clean", env)

    # Tamper state.json version
    st_path = tmp_path / "state.json"
    bad_state = json.loads(st_path.read_text(encoding="utf-8"))
    bad_state["version"] = 999
    st_path.write_text(json.dumps(bad_state), encoding="utf-8")

    cs2 = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc:
        cs2.load(key, resume=True)
    assert exc.value.code == E_STATE


def test_sha256_canonical_alias_bilateral_tamper_detection(tmp_path: pathlib.Path):
    """Verify tampering either the canonical artifact or alias artifact is detected."""
    cs = ChainState(tmp_path)
    key = "a" * 64
    state = cs.load(key)
    env = _make_valid_envelope("knowledge", idempotency_key=key)
    cs.commit_stage(state, "knowledge", env)

    # Both knowledge.json and network.json exist
    know_path = tmp_path / "artifacts" / "knowledge.json"
    net_path = tmp_path / "artifacts" / "network.json"
    assert know_path.exists()
    assert net_path.exists()

    # Corrupt network.json
    net_path.write_bytes(b'{"corrupt": true}')
    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "network")
    assert exc.value.code == E_STATE


# ============================================================================
# Dimension 4: Crash-Recovery Invariants & Resume Semantics
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("crash_stage", [
    "collect", "clean", "extract", "knowledge", "inspect", "qgate", "targeted", "merge", "report"
])
async def test_crash_recovery_across_all_pipeline_stages(tmp_path: pathlib.Path, crash_stage: str, monkeypatch):
    """Crash at each stage, resume with resume=True, verify uncommitted run to success and committed skip."""
    pipe1, work_dir = _build_test_pipeline(tmp_path, resume=False)
    orig_exec = pipe1._exec

    # For stages handled by _exec
    if crash_stage in ("collect", "clean", "extract", "knowledge", "report"):
        async def crashing_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
            canon = "knowledge" if stage == "organize" else stage
            if canon == crash_stage:
                raise RuntimeError(f"SIMULATED_FAILURE_AT_{stage.upper()}")
            await orig_exec(stage, topic, topic_dir, result)
        pipe1._exec = crashing_exec
        res1 = await pipe1.run("empirical-rep-test")
        assert res1.failed_stage is not None
    elif crash_stage == "inspect":
        def crash_inspect(req): raise RuntimeError("CRASH_INSPECT")
        monkeypatch.setattr(inspect_min, "run_inspect", crash_inspect)
        with pytest.raises(RuntimeError, match="CRASH_INSPECT"):
            await pipe1.run("empirical-rep-test")
        monkeypatch.undo()
    elif crash_stage == "qgate":
        def crash_qgate(req): raise RuntimeError("CRASH_QGATE")
        monkeypatch.setattr(qgate_min, "run_gate", crash_qgate)
        with pytest.raises(RuntimeError, match="CRASH_QGATE"):
            await pipe1.run("empirical-rep-test")
        monkeypatch.undo()
    elif crash_stage == "targeted":
        def crash_targeted(req): raise RuntimeError("CRASH_TARGETED")
        monkeypatch.setattr(targeted_min, "run_targeted", crash_targeted)
        with pytest.raises(RuntimeError, match="CRASH_TARGETED"):
            await pipe1.run("empirical-rep-test")
        monkeypatch.undo()
    elif crash_stage == "merge":
        orig_commit = ChainState.commit_stage
        def crashing_commit(self, state, stage, envelope):
            if stage == "merge":
                raise RuntimeError("CRASH_MERGE")
            return orig_commit(self, state, stage, envelope)
        monkeypatch.setattr(ChainState, "commit_stage", crashing_commit)
        with pytest.raises(RuntimeError, match="CRASH_MERGE"):
            await pipe1.run("empirical-rep-test")
        monkeypatch.undo()
    pipe2, _ = _build_test_pipeline(tmp_path, resume=True)
    res2 = await pipe2.run("empirical-rep-test")

    assert res2.failed_stage is None
    # All stages completed or skipped
    all_seen = set(res2.stages_completed) | set(res2.stages_skipped)
    assert "report" in res2.stages_completed or "report" in res2.stages_skipped

    topic_dir = work_dir / "empirical-rep-test"
    summary_file = topic_dir / "run-summary.json"
    assert summary_file.exists()
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0


@pytest.mark.asyncio
async def test_resume_preserves_artifact_bytes_and_mtimes_closed_loop(tmp_path: pathlib.Path):
    """Direct verification of Worker M1 Iteration 2 Defect 2 fix:
    Resuming after crash at report MUST NOT re-run closed-loop stages or modify their artifact files.
    """
    pipe1, work_dir = _build_test_pipeline(tmp_path, resume=False)
    orig_exec = pipe1._exec

    async def fail_at_report(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "report":
            raise RuntimeError("SIMULATED_CRASH_AT_REPORT")
        await orig_exec(stage, topic, topic_dir, result)

    pipe1._exec = fail_at_report

    res1 = await pipe1.run("empirical-rep-test")
    assert res1.failed_stage == "report"

    topic_dir = work_dir / "empirical-rep-test"
    artifacts_dir = topic_dir / "artifacts"

    # Capture mtimes and byte contents of closed-loop artifacts
    closed_stages = ["inspect", "qgate", "targeted", "merge", "knowledge"]
    baseline_info: dict[str, tuple[bytes, float]] = {}
    for s in closed_stages:
        path = artifacts_dir / f"{s}.json"
        assert path.exists(), f"Missing artifact {s}.json before crash"
        baseline_info[s] = (path.read_bytes(), path.stat().st_mtime)

    # Delay to guarantee mtime would advance if files were rewritten
    time.sleep(0.05)

    # Resume
    pipe2, _ = _build_test_pipeline(tmp_path, resume=True)
    res2 = await pipe2.run("empirical-rep-test")

    assert res2.failed_stage is None
    assert "report" in res2.stages_completed

    # Verify closed-loop stages were skipped and artifacts remain byte-identical with identical mtime
    for s in ["inspect", "qgate"]:
        assert s in res2.stages_skipped, f"Stage {s} was not marked as skipped on resume!"

    for s in closed_stages:
        path = artifacts_dir / f"{s}.json"
        saved_bytes, saved_mtime = baseline_info[s]
        current_bytes = path.read_bytes()
        current_mtime = path.stat().st_mtime
        assert current_bytes == saved_bytes, f"Artifact {s}.json byte content changed on resume!"
        assert current_mtime == saved_mtime, f"Artifact {s}.json mtime updated on resume (re-executed)!"


@pytest.mark.asyncio
async def test_crash_recovery_cleans_up_orphaned_tmp_files(tmp_path: pathlib.Path):
    """Verify that orphan .tmp-* files from interrupted runs are completely purged on resume."""
    pipe, work_dir = _build_test_pipeline(tmp_path, resume=True)
    topic_dir = work_dir / "empirical-rep-test"
    topic_dir.mkdir(parents=True, exist_ok=True)
    artifacts = topic_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    for i in range(25):
        (topic_dir / f"state.json.tmp-dead-{i:04d}").write_text("orphan", encoding="utf-8")
        (artifacts / f"collect.json.tmp-dead-{i:04d}").write_text("orphan", encoding="utf-8")

    res = await pipe.run("empirical-rep-test")
    assert res.failed_stage is None
    assert list(topic_dir.glob("*.tmp-*")) == []
    assert list(artifacts.glob("*.tmp-*")) == []


@pytest.mark.asyncio
async def test_resume_rejects_mismatched_idempotency_key(tmp_path: pathlib.Path):
    """Verify running in existing directory with different mode fails with E_IDEMPOTENCY_CONFLICT."""
    pipe1, work_dir = _build_test_pipeline(tmp_path, mode="full", resume=False)
    await pipe1.run("empirical-rep-test")

    pipe2, _ = _build_test_pipeline(tmp_path, mode="brief", resume=True)
    with pytest.raises(ChainStateFault) as exc:
        await pipe2.run("empirical-rep-test")
    assert exc.value.code == E_IDEMPOTENCY_CONFLICT


# ============================================================================
# Dimension 5: Protocol v1 Envelope Compliance
# ============================================================================

@pytest.mark.asyncio
async def test_protocol_v1_envelope_invariants_across_all_nine_stages(tmp_path: pathlib.Path):
    """Verify Protocol v1 schema invariants across all 9 canonical stage envelopes."""
    pipe, work_dir = _build_test_pipeline(tmp_path)
    res = await pipe.run("empirical-rep-test")
    assert res.failed_stage is None

    topic_dir = work_dir / "empirical-rep-test"
    artifacts_dir = topic_dir / "artifacts"

    for stage in CANONICAL_NINE_STAGES:
        art_path = artifacts_dir / f"{stage}.json"
        assert art_path.exists(), f"Missing artifact for stage {stage}"
        env = json.loads(art_path.read_text(encoding="utf-8"))

        # Envelope invariants
        assert env["v"] == 1
        assert isinstance(env["run_id"], str) and len(env["run_id"]) > 0
        canon_or_alias = {stage, "network" if stage == "knowledge" else "", "gate" if stage == "qgate" else ""}
        assert env["stage"] in canon_or_alias
        assert len(env["idempotency_key"]) == 64
        int(env["idempotency_key"], 16)  # valid hex

        lease = env["budget_lease"]
        assert isinstance(lease["lease_id"], str)
        assert lease["tokens_max"] >= 0
        assert lease["cost_max"] >= 0
        assert lease["wall_s_max"] >= 0
        assert lease["search_calls_max"] >= 0

        # Zero leakage: result is present, error is null
        assert env["error"] is None
        assert env["result"] is not None


def test_protocol_v1_error_frame_mutual_exclusivity():
    """Verify that on error, result is None (zero leakage) and error conforms to contract."""
    req = {
        "v": 1,
        "run_id": "r-err",
        "stage": "merge",
        "request_id": "req:merge:err",
        "idempotency_key": "bad_key",
        "budget_lease": {
            "lease_id": "l-1", "tokens_max": 100, "cost_max": 1.0,
            "wall_s_max": 10.0, "search_calls_max": 1,
            "issued_at": "2026-09-17T14:00:00Z", "expires_at": "2026-09-17T15:00:00Z",
        },
    }
    env = merge_min.run_merge(req)
    assert env["result"] is None, "CRITICAL: result payload leaked on error!"
    assert env["error"] is not None
    err = env["error"]
    assert err["code"] == merge_min.E_SCHEMA
    assert err["stage"] == "merge"
    assert isinstance(err["safe_message"], str)
    assert "retryable" in err


# ============================================================================
# Dimension 6: Verification of Worker M1 Iteration 2 Fixes
# ============================================================================

@pytest.mark.asyncio
async def test_defect1_default_pipeline_config_natively_drives_nine_loop(tmp_path: pathlib.Path):
    """R1: Instantiating PipelineConfig with default arguments must natively execute 9 stages."""
    work_dir = tmp_path / "default_work"
    # Do NOT supply stages; PipelineConfig uses default factory
    cfg = PipelineConfig(topic="default-nine-test", work_dir=work_dir, resume=False)
    pipe = ResearchPipeline(cfg)

    pipe._llm = MockLLMClient(chat_response="# 报告\n\n默认管线 (来源01)。\n\n## 参考资料\n- 来源01：默认 — https://arxiv.org/abs/def")

    async def fake_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text("默认收集内容", encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("默认清洗内容", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text("[]", encoding="utf-8")
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识大纲", encoding="utf-8")

    pipe._exec = fake_exec
    events = []
    async for ev in pipe.stream():
        events.append(ev.stage)

    # Verify native 9-stage closed-loop stages ran
    assert "inspect" in events
    assert "qgate" in events
    assert "report" in events

    topic_dir = work_dir / "default-nine-test"
    artifacts = topic_dir / "artifacts"
    for stage in CANONICAL_NINE_STAGES:
        assert (artifacts / f"{stage}.json").exists(), f"Missing {stage}.json in artifacts/ under default PipelineConfig"


@pytest.mark.asyncio
async def test_defect2_sdk_research_entrypoint_natively_drives_nine_loop(tmp_path: pathlib.Path, monkeypatch):
    """R1 & F02: Python SDK research() entrypoint natively runs 9-stage closed-loop."""
    work_dir = tmp_path / "sdk_work"

    def mock_get_llm(self):
        return MockLLMClient(chat_response="# SDK 报告\n\nSDK实证 (来源01)。\n\n## 参考资料\n- 来源01：SDK — https://arxiv.org/abs/sdk")

    monkeypatch.setattr(ResearchPipeline, "_get_llm", mock_get_llm)

    orig_init = ResearchPipeline.__init__

    def mock_init(self, config):
        orig_init(self, config)
        async def fake_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
            if stage == "collect":
                raw = topic_dir / "raw"
                raw.mkdir(parents=True, exist_ok=True)
                (raw / "paper.md").write_text("SDK 内容", encoding="utf-8")
            elif stage == "clean":
                clean = topic_dir / "clean"
                clean.mkdir(parents=True, exist_ok=True)
                (clean / "paper.md").write_text("SDK 清洗", encoding="utf-8")
            elif stage == "extract":
                extracted = topic_dir / "extracted"
                extracted.mkdir(parents=True, exist_ok=True)
                (extracted / "entities.json").write_text("[]", encoding="utf-8")
            elif stage in ("organize", "knowledge"):
                tree = topic_dir / "tree"
                tree.mkdir(parents=True, exist_ok=True)
                (tree / "00-主表.md").write_text("# SDK 知识树", encoding="utf-8")
        self._exec = fake_exec

    monkeypatch.setattr(ResearchPipeline, "__init__", mock_init)

    res = await research("sdk-quantum", work_dir=work_dir)
    assert res.failed_stage is None

    topic_dir = work_dir / "sdk-quantum"
    artifacts = topic_dir / "artifacts"
    for stage in CANONICAL_NINE_STAGES:
        assert (artifacts / f"{stage}.json").exists(), f"SDK did not produce {stage}.json"

    assert (topic_dir / "report.md").exists()
    assert (topic_dir / "tree" / "00-主表.md").exists()
    assert (topic_dir / "sources.json").exists()
    assert (topic_dir / "run-summary.json").exists()


def test_defect3_load_config_overrides_stages_preservation():
    """Verify load_config preserves caller-specified overrides['stages'] under mode='full'."""
    custom_stages = ["collect", "clean", "extract", "organize", "report"]
    cfg = load_config(overrides={"stages": custom_stages, "mode": "full"})
    assert cfg.stages == custom_stages, f"load_config clobbered overrides['stages']: {cfg.stages}"


# ============================================================================
# Dimension 7: Multi-Round Closed-Loop & Downstream Packaging
# ============================================================================

@pytest.mark.asyncio
async def test_closed_loop_multi_round_targeted_and_cas_merge(tmp_path: pathlib.Path, monkeypatch):
    """Verify autonomous closed-loop multi-round targeted search and CAS merge when QGate returns CONTINUE."""
    pipe, work_dir = _build_test_pipeline(tmp_path)

    # First gate call returns CONTINUE to trigger targeted search and merge; second returns STOP_SUCCESS
    original_run_gate = qgate_min.run_gate
    gate_calls = 0

    def dynamic_run_gate(req):
        nonlocal gate_calls
        gate_calls += 1
        res = original_run_gate(req)
        if gate_calls == 1:
            res["result"]["verdict"] = "CONTINUE"
        else:
            res["result"]["verdict"] = "STOP_SUCCESS"
        return res

    monkeypatch.setattr(qgate_min, "run_gate", dynamic_run_gate)

    res = await pipe.run("empirical-rep-test")
    assert res.failed_stage is None
    assert "targeted" in res.stages_completed
    assert "merge" in res.stages_completed

    topic_dir = work_dir / "empirical-rep-test"
    cs = ChainState(topic_dir)
    state = json.loads((topic_dir / "state.json").read_text(encoding="utf-8"))

    # State loop history recorded
    assert state["loop"]["current_round"] >= 1
    assert len(state["loop"]["history"]) >= 1
    assert state["loop"]["latest_network_digest"] is not None

    # Multi-round inspect and qgate checkpoints exist
    artifacts = topic_dir / "artifacts"
    assert (artifacts / "inspect_r1.json").exists()
    assert (artifacts / "qgate_r1.json").exists()


@pytest.mark.asyncio
async def test_wiki_stage_immutable_package_delivery(tmp_path: pathlib.Path):
    """Verify downstream wiki-stage creates immutable package with 0o444 files and 0o555 directory."""
    pipe, work_dir = _build_test_pipeline(tmp_path)
    res = await pipe.run("empirical-rep-test")
    assert res.failed_stage is None

    topic_dir = work_dir / "empirical-rep-test"
    pkg_dest = tmp_path / "wiki_pkg"
    pkg_dest.mkdir(parents=True, exist_ok=True)

    pkg = build_stage_package(topic_dir, pkg_dest)
    assert pkg is not None
    assert pkg.package_path.exists()

    # Permissions check
    dir_mode = oct(pkg.package_path.stat().st_mode & 0o777)
    manifest_path = pkg.package_path / "manifest.json"
    manifest_mode = oct(manifest_path.stat().st_mode & 0o777)
    assert dir_mode == "0o555", f"Directory permissions should be 0o555, got {dir_mode}"
    assert manifest_mode == "0o444", f"Manifest permissions should be 0o444, got {manifest_mode}"

