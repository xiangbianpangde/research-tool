"""Empirical Adversarial Challenger Test Suite for Milestone 1.

Targeting:
1. Atomic CAS write-if-match merge semantics (conflict coexist, dedup, branch racing, immutability).
2. Concurrent state commits and write_atomic integrity under multi-threaded contention.
3. SHA-256 tamper detection in ChainState (bit flips, whitespace changes, zero-byte artifacts, manifest mismatches, alias tampering).
4. Crash-recovery invariants across all 9 stages, byte-identical artifact preservation, orphan cleanup, idempotency enforcement.
5. Protocol v1 envelope compliance (schema invariants, mutual exclusivity of result/error, error frame contracts).
6. Downstream deliverable integrity and SDK native 9-stage execution.
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


# ============================================================================
# Helpers & Fixtures
# ============================================================================

def _create_mock_pipeline(tmp_path: pathlib.Path, **kwargs: Any) -> tuple[ResearchPipeline, pathlib.Path]:
    work_dir = tmp_path / "work"
    cfg_kwargs = {
        "topic": "challenger-empirical",
        "work_dir": work_dir,
        "mode": "full",
        "resume": False,
    }
    cfg_kwargs.update(kwargs)
    cfg = PipelineConfig(**cfg_kwargs)
    pipe = ResearchPipeline(cfg)

    pipe._llm = MockLLMClient(
        chat_response="# 报告\n\n实证核验完成，具备自洽性 (来源01)。\n\n## 参考资料\n- 来源01：实证核验 — https://arxiv.org/abs/challenger-empirical"
    )

    async def mock_exec(stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text(
                "<!-- source: https://arxiv.org/abs/challenger-empirical -->\n"
                "<!-- fetched: 2026-09-17T14:00:00Z -->\n实证数据内容",
                encoding="utf-8",
            )
            sources = [
                {
                    "url": "https://arxiv.org/abs/challenger-empirical",
                    "title": "实证核验",
                    "fetchedAt": "2026-09-17T14:00:00Z",
                    "content_hash": "sha256:" + "f" * 64,
                }
            ]
            (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("清洗后实证数据内容", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text(
                '[{"name": "Empirical Verification", "type": "method"}]', encoding="utf-8"
            )
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络大纲\n\n- [[N01-实证|实证核验概述]]\n", encoding="utf-8")
            (tree / "N01-实证.md").write_text(
                "<!-- source: https://arxiv.org/abs/challenger-empirical -->\n"
                "<!-- fetched: 2026-09-17T14:00:00Z -->\n# 实证\n节点内容",
                encoding="utf-8",
            )
        elif stage == "report":
            pass

    pipe._exec = mock_exec
    return pipe, work_dir


def _make_sample_network(source_ids: list[str]) -> dict[str, Any]:
    nodes = []
    for sid in source_ids:
        nodes.append(
            {
                "node_id": sid,
                "evidence_spans": [
                    {
                        "locator": f"https://arxiv.org/abs/{sid}",
                        "content_sha256": hashlib.sha256(sid.encode("utf-8")).hexdigest(),
                        "round_id": 0,
                    }
                ],
            }
        )
    return {
        "nodes": sorted(nodes, key=lambda n: n["node_id"]),
        "edges": [],
        "counts": {"nodes": len(nodes), "edges": 0},
    }


def _make_lease() -> dict[str, Any]:
    return {
        "lease_id": "lease-empirical-001",
        "tokens_max": 50000,
        "cost_max": 2.5,
        "wall_s_max": 60.0,
        "search_calls_max": 10,
        "issued_at": "2026-09-17T14:00:00Z",
        "expires_at": "2026-09-17T15:00:00Z",
    }


# ============================================================================
# Dimension 1: Atomic CAS Write-If-Match Merge Semantics
# ============================================================================

def test_cas_merge_success_with_exact_prev_digest():
    """Verify CAS merge succeeds when prev_digest exactly matches graph_digest."""
    graph = _make_sample_network(["src:01", "src:02"])
    prev_digest = merge_min.graph_digest(graph)

    responses = [
        {
            "request_id": "req-01",
            "facts": [
                {
                    "source_id": "src:03",
                    "locator": "https://arxiv.org/abs/src:03",
                    "content_sha256": hashlib.sha256(b"src:03").hexdigest(),
                }
            ],
        }
    ]

    net_env = {
        "v": 1,
        "run_id": "run-cas-test",
        "stage": "network",
        "request_id": "net:0",
        "idempotency_key": prev_digest,
        "budget_lease": _make_lease(),
        "result": graph,
        "error": None,
    }

    req = merge_min.request_from_chain(net_env, responses, prev_digest=prev_digest)
    res_env = merge_min.run_merge(req)

    assert res_env["error"] is None
    assert res_env["result"] is not None
    result = res_env["result"]
    assert result["counts"]["new_facts"] == 1
    assert result["counts"]["skipped"] == 0
    assert result["counts"]["conflicts"] == 0
    assert result["latest_digest"] == merge_min.graph_digest(result["graph"])
    assert any(n["node_id"] == "src:03" for n in result["graph"]["nodes"])


def test_cas_merge_rejects_stale_or_corrupted_digest():
    """Verify CAS write-if-match rejects stale/mismatched prev_digest with E_CAS_CONFLICT."""
    graph = _make_sample_network(["src:01"])
    stale_digest = "e" * 64

    responses = [
        {
            "request_id": "req-02",
            "facts": [
                {
                    "source_id": "src:02",
                    "locator": "https://arxiv.org/abs/src:02",
                    "content_sha256": hashlib.sha256(b"src:02").hexdigest(),
                }
            ],
        }
    ]

    net_env = {
        "v": 1,
        "run_id": "run-cas-fail",
        "stage": "network",
        "request_id": "net:0",
        "idempotency_key": merge_min.graph_digest(graph),
        "budget_lease": _make_lease(),
        "result": graph,
        "error": None,
    }

    req = merge_min.request_from_chain(net_env, responses, prev_digest=stale_digest)
    res_env = merge_min.run_merge(req)

    # Invariant: error is populated, result is None
    assert res_env["result"] is None
    assert res_env["error"] is not None
    assert res_env["error"]["code"] == E_CAS_CONFLICT
    assert "write-if-match" in res_env["error"]["safe_message"]

    # Invariant: direct call raises MergeStageFault
    with pytest.raises(merge_min.MergeStageFault) as exc_info:
        merge_min.merge(graph, responses, prev_digest=stale_digest)
    assert exc_info.value.frame["code"] == E_CAS_CONFLICT


def test_cas_merge_conflict_coexist_semantics():
    """Verify conflicting evidence for the same source_id preserves BOTH spans under conflict_coexist."""
    graph = _make_sample_network(["src:conflict"])
    initial_digest = merge_min.graph_digest(graph)

    # Two distinct facts claiming different content_sha256 for the same source_id
    responses = [
        {
            "request_id": "req-conflict-1",
            "facts": [
                {
                    "source_id": "src:conflict",
                    "locator": "https://arxiv.org/abs/src:conflict#v2",
                    "content_sha256": hashlib.sha256(b"counter-evidence-1").hexdigest(),
                },
                {
                    "source_id": "src:conflict",
                    "locator": "https://arxiv.org/abs/src:conflict#v3",
                    "content_sha256": hashlib.sha256(b"counter-evidence-2").hexdigest(),
                },
            ],
        }
    ]

    net_env = {
        "v": 1,
        "run_id": "run-conflict",
        "stage": "network",
        "request_id": "net:0",
        "idempotency_key": initial_digest,
        "budget_lease": _make_lease(),
        "result": graph,
        "error": None,
    }

    req = merge_min.request_from_chain(net_env, responses, prev_digest=initial_digest)
    res_env = merge_min.run_merge(req)

    assert res_env["error"] is None
    res = res_env["result"]
    assert res["counts"]["conflicts"] == 2
    assert res["counts"]["new_facts"] == 2

    # Node src:conflict must contain 1 original + 2 conflict = 3 evidence spans
    node = next(n for n in res["graph"]["nodes"] if n["node_id"] == "src:conflict")
    assert len(node["evidence_spans"]) == 3
    # Verify deterministic sorting of evidence_spans
    locators = [s["locator"] for s in node["evidence_spans"]]
    assert locators == sorted(locators)

    # Verify merge_log records conflict_coexist entries
    coexist_logs = [log for log in res["merge_log"] if log["kind"] == "conflict_coexist"]
    assert len(coexist_logs) == 2


def test_cas_merge_content_sha256_dedup():
    """Verify merging identical (source_id, content_sha256) facts skips duplicates cleanly."""
    graph = _make_sample_network(["src:dedup"])
    span0 = graph["nodes"][0]["evidence_spans"][0]
    initial_digest = merge_min.graph_digest(graph)

    # Present duplicate fact identical to what is already in graph
    dup_responses = [
        {
            "request_id": "req-dup",
            "facts": [
                {
                    "source_id": "src:dedup",
                    "locator": span0["locator"],
                    "content_sha256": span0["content_sha256"],
                }
            ],
        }
    ]

    req = merge_min.request_from_chain(
        {
            "v": 1,
            "run_id": "run-dup",
            "stage": "network",
            "request_id": "net:0",
            "idempotency_key": initial_digest,
            "budget_lease": _make_lease(),
            "result": graph,
            "error": None,
        },
        dup_responses,
        prev_digest=initial_digest,
    )
    res_env = merge_min.run_merge(req)

    assert res_env["error"] is None
    res = res_env["result"]
    assert res["counts"]["skipped"] == 1
    assert res["counts"]["new_facts"] == 0
    # Graph remains identical to input
    assert len(res["graph"]["nodes"][0]["evidence_spans"]) == 1
    assert res["latest_digest"] == initial_digest


def test_cas_merge_caller_immutability():
    """Verify merge NEVER mutates caller-owned network dict in-place."""
    graph = _make_sample_network(["src:immutable"])
    graph_copy = copy.deepcopy(graph)
    initial_digest = merge_min.graph_digest(graph)

    responses = [
        {
            "request_id": "req-mut",
            "facts": [
                {
                    "source_id": "src:new",
                    "locator": "https://arxiv.org/abs/src:new",
                    "content_sha256": hashlib.sha256(b"new").hexdigest(),
                }
            ],
        }
    ]

    merge_min.merge(graph, responses, prev_digest=initial_digest)
    # Original graph dict must remain bit-for-bit identical
    assert graph == graph_copy
    assert merge_min.graph_digest(graph) == initial_digest


def test_cas_merge_branch_race_and_rebase_simulation():
    """Simulate two concurrent branches branching from V0: Branch A commits V1, Branch B gets rejected and rebases to V2."""
    v0_graph = _make_sample_network(["src:base"])
    v0_digest = merge_min.graph_digest(v0_graph)

    resp_a = [
        {
            "request_id": "tq:branch_a",
            "facts": [{"source_id": "src:a", "locator": "https://a.com", "content_sha256": "a" * 64}],
        }
    ]
    resp_b = [
        {
            "request_id": "tq:branch_b",
            "facts": [{"source_id": "src:b", "locator": "https://b.com", "content_sha256": "b" * 64}],
        }
    ]

    # Branch A commits from V0
    v1_res = merge_min.merge(v0_graph, resp_a, prev_digest=v0_digest)
    v1_graph = v1_res["graph"]
    v1_digest = v1_res["latest_digest"]

    # Branch B attempts to commit against V1 using its stale v0_digest
    with pytest.raises(merge_min.MergeStageFault) as exc_info:
        merge_min.merge(v1_graph, resp_b, prev_digest=v0_digest)
    assert exc_info.value.frame["code"] == E_CAS_CONFLICT

    # Branch B re-fetches latest digest (v1_digest) and retries -> succeeds
    v2_res = merge_min.merge(v1_graph, resp_b, prev_digest=v1_digest)
    assert v2_res["counts"]["new_facts"] == 1
    # Final V2 graph has both src:a and src:b
    v2_nodes = {n["node_id"] for n in v2_res["graph"]["nodes"]}
    assert {"src:base", "src:a", "src:b"}.issubset(v2_nodes)


# ============================================================================
# Dimension 2: Concurrent State Commits & Atomic File Replacement
# ============================================================================

def test_concurrent_write_atomic_stress_50_threads(tmp_path: pathlib.Path):
    """Stress test write_atomic under heavy multi-threaded contention (50 workers).
    Invariants:
    1. Zero dangling .tmp-* files remain.
    2. Target file is always intact, valid JSON, matching SHA-256.
    3. Never corrupted or 0 bytes.
    """
    target = tmp_path / "concurrent" / "stress_target.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    num_threads = 50
    rounds_per_thread = 10

    def worker(worker_id: int):
        for r in range(rounds_per_thread):
            payload = {
                "worker_id": worker_id,
                "round": r,
                "timestamp": time.time(),
                "data": "x" * 1024,
            }
            raw = json.dumps(payload).encode("utf-8")
            write_atomic(target, raw)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Invariant 1: No leftover temp files in folder
    tmp_files = list(target.parent.glob("*.tmp-*"))
    assert tmp_files == [], f"Found lingering temporary files: {tmp_files}"

    # Invariant 2: Target file exists and is valid JSON
    assert target.exists()
    content = target.read_bytes()
    assert len(content) > 0
    parsed = json.loads(content.decode("utf-8"))
    assert "worker_id" in parsed
    assert "round" in parsed

    # Invariant 3: SHA-256 matches content
    assert sha256_bytes(content) == hashlib.sha256(content).hexdigest()


def test_concurrent_multi_stage_chain_state_commits(tmp_path: pathlib.Path):
    """Multiple concurrent workers committing distinct stages to the same ChainState directory.
    All stage artifacts must be safely persisted, uncorrupted, with zero temp files.
    """
    cs = ChainState(tmp_path)
    input_key = "concurrent_key_" + "0" * 50
    state = cs.load(input_key, resume=False)

    num_stages = 20

    def commit_worker(idx: int):
        stage_name = f"custom_stage_{idx:02d}"
        envelope = {
            "v": 1,
            "run_id": "run-concurrent",
            "stage": stage_name,
            "request_id": f"req:{idx}",
            "idempotency_key": hashlib.sha256(stage_name.encode("utf-8")).hexdigest(),
            "budget_lease": _make_lease(),
            "result": {"idx": idx, "payload": "stage_data_" * 10},
            "error": None,
        }
        # Commit stage
        target = cs.artifacts / f"{stage_name}.json"
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        write_atomic(target, raw)
        return stage_name, raw

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(commit_worker, i) for i in range(num_stages)]
        results = [f.result() for f in futures]

    assert cs.cleanup_tmps() == 0

    # Verify all artifacts on disk
    for stage_name, expected_bytes in results:
        art_path = cs.artifacts / f"{stage_name}.json"
        assert art_path.exists()
        actual_bytes = art_path.read_bytes()
        assert actual_bytes == expected_bytes
        assert sha256_bytes(actual_bytes) == hashlib.sha256(expected_bytes).hexdigest()


# ============================================================================
# Dimension 3: SHA-256 Tamper Detection in ChainState
# ============================================================================

def test_tamper_detection_single_bit_flip_in_artifact(tmp_path: pathlib.Path):
    """Flipping a single bit in a committed stage artifact causes verify_state & read_stage to reject with E_STATE."""
    cs = ChainState(tmp_path)
    input_key = "tamper_key_" + "1" * 53
    state = cs.load(input_key, resume=False)

    env = {
        "v": 1,
        "run_id": "run-tamper",
        "stage": "extract",
        "request_id": "req:extract",
        "idempotency_key": input_key,
        "budget_lease": _make_lease(),
        "result": {"verified": True},
        "error": None,
    }
    cs.commit_stage(state, "extract", env)

    art_path = cs.artifacts / "extract.json"
    original_bytes = bytearray(art_path.read_bytes())

    # Flip the lowest bit of the first byte
    original_bytes[0] ^= 1
    art_path.write_bytes(bytes(original_bytes))

    # Verification must fail fast
    with pytest.raises(ChainStateFault) as exc_read:
        cs.read_stage(state, "extract")
    assert exc_read.value.code == E_STATE

    with pytest.raises(ChainStateFault) as exc_verify:
        cs.verify_state(state)
    assert exc_verify.value.code == E_STATE

    # Load with resume=True must fail fast
    cs_resumed = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc_load:
        cs_resumed.load(input_key, resume=True)
    assert exc_load.value.code == E_STATE


def test_tamper_detection_trailing_whitespace_modification(tmp_path: pathlib.Path):
    """Appending a single newline or space to an artifact alters SHA-256 and is detected as E_STATE."""
    cs = ChainState(tmp_path)
    input_key = "tamper_ws_" + "2" * 54
    state = cs.load(input_key, resume=False)

    env = {
        "v": 1,
        "run_id": "run-tamper-ws",
        "stage": "clean",
        "request_id": "req:clean",
        "idempotency_key": input_key,
        "budget_lease": _make_lease(),
        "result": {"cleaned": 123},
        "error": None,
    }
    cs.commit_stage(state, "clean", env)

    art_path = cs.artifacts / "clean.json"
    art_path.write_bytes(art_path.read_bytes() + b"\n")

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "clean")
    assert exc.value.code == E_STATE


def test_tamper_detection_zero_byte_empty_artifact(tmp_path: pathlib.Path):
    """Truncating an artifact to 0 bytes raises E_STATE."""
    cs = ChainState(tmp_path)
    input_key = "tamper_zero_" + "3" * 52
    state = cs.load(input_key, resume=False)

    env = {
        "v": 1,
        "run_id": "run-zero",
        "stage": "collect",
        "request_id": "req:collect",
        "idempotency_key": input_key,
        "budget_lease": _make_lease(),
        "result": {"items": []},
        "error": None,
    }
    cs.commit_stage(state, "collect", env)

    art_path = cs.artifacts / "collect.json"
    art_path.write_bytes(b"")

    with pytest.raises(ChainStateFault) as exc:
        cs.verify_state(state)
    assert exc.value.code == E_STATE


def test_tamper_detection_deleted_artifact_with_done_manifest(tmp_path: pathlib.Path):
    """Deleting an artifact while state.json lists it as done raises E_STATE missing artifact."""
    cs = ChainState(tmp_path)
    input_key = "tamper_del_" + "4" * 53
    state = cs.load(input_key, resume=False)

    env = {
        "v": 1,
        "run_id": "run-del",
        "stage": "inspect",
        "request_id": "req:inspect",
        "idempotency_key": input_key,
        "budget_lease": _make_lease(),
        "result": {"findings": []},
        "error": None,
    }
    cs.commit_stage(state, "inspect", env)

    art_path = cs.artifacts / "inspect.json"
    art_path.unlink()

    with pytest.raises(ChainStateFault) as exc:
        cs.verify_state(state)
    assert exc.value.code == E_STATE
    assert "missing artifact" in exc.value.safe_message


def test_tamper_detection_on_canonical_aliases_knowledge_network(tmp_path: pathlib.Path):
    """Verify tampering with alias mirrored artifacts (knowledge/network) is detected via either alias name."""
    cs = ChainState(tmp_path)
    input_key = "tamper_alias_" + "5" * 51
    state = cs.load(input_key, resume=False)

    graph = _make_sample_network(["src:alias"])
    env = {
        "v": 1,
        "run_id": "run-alias",
        "stage": "knowledge",
        "request_id": "req:know",
        "idempotency_key": input_key,
        "budget_lease": _make_lease(),
        "result": graph,
        "error": None,
    }
    cs.commit_stage(state, "knowledge", env)

    # Both knowledge.json and network.json exist
    assert (cs.artifacts / "knowledge.json").exists()
    assert (cs.artifacts / "network.json").exists()

    # Tamper network.json
    (cs.artifacts / "network.json").write_bytes(b'{"tampered_alias": true}')

    # Reading stage via alias 'network' must fail with E_STATE
    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "network")
    assert exc.value.code == E_STATE


# ============================================================================
# Dimension 4: Crash-Recovery & Resume Invariants Across All 9 Stages
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("crash_stage", [
    "collect",
    "clean",
    "extract",
    "knowledge",
    "inspect",
    "qgate",
    "targeted",
    "merge",
    "report",
])
async def test_crash_recovery_parameterized_stages(tmp_path: pathlib.Path, crash_stage: str, monkeypatch):
    """Crash at any stage N; resume=True must resume and complete the rest of the pipeline to 1.0 citation coverage."""
    pipe1, work_dir = _create_mock_pipeline(tmp_path, resume=False)
    topic_dir = work_dir / "challenger-empirical"
    orig_exec = pipe1._exec

    # Inject crash based on the target stage
    if crash_stage in ("collect", "clean", "extract", "knowledge", "report"):
        async def crashing_exec(stage: str, topic: str, td: pathlib.Path, res: PipelineResult):
            if stage == crash_stage or (crash_stage == "knowledge" and stage in ("knowledge", "organize")):
                raise RuntimeError(f"SIMULATED_CRASH_AT_{crash_stage.upper()}")
            await orig_exec(stage, topic, td, res)
        pipe1._exec = crashing_exec
        res1 = await pipe1.run("challenger-empirical")
        assert res1.failed_stage in (crash_stage, "organize" if crash_stage == "knowledge" else crash_stage)
    elif crash_stage == "inspect":
        def crashing_inspect(req):
            raise RuntimeError("SIMULATED_CRASH_AT_INSPECT")
        monkeypatch.setattr(inspect_min, "run_inspect", crashing_inspect)
        with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_INSPECT"):
            await pipe1.run("challenger-empirical")
        monkeypatch.undo()
    elif crash_stage == "qgate":
        def crashing_gate(req):
            raise RuntimeError("SIMULATED_CRASH_AT_QGATE")
        monkeypatch.setattr(qgate_min, "run_gate", crashing_gate)
        with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_QGATE"):
            await pipe1.run("challenger-empirical")
        monkeypatch.undo()
    elif crash_stage == "targeted":
        def crashing_target(req):
            raise RuntimeError("SIMULATED_CRASH_AT_TARGETED")
        monkeypatch.setattr(targeted_min, "run_targeted", crashing_target)
        with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_TARGETED"):
            await pipe1.run("challenger-empirical")
        monkeypatch.undo()
    elif crash_stage == "merge":
        orig_commit = ChainState.commit_stage
        def crashing_commit(self, state, stage, envelope):
            if stage == "merge":
                raise RuntimeError("SIMULATED_CRASH_AT_MERGE")
            return orig_commit(self, state, stage, envelope)
        monkeypatch.setattr(ChainState, "commit_stage", crashing_commit)
        with pytest.raises(RuntimeError, match="SIMULATED_CRASH_AT_MERGE"):
            await pipe1.run("challenger-empirical")
        monkeypatch.undo()

    # Verify that stages preceding crash_stage are recorded in state.json
    cs = ChainState(topic_dir)
    st = json.loads((topic_dir / "state.json").read_text())
    preceding_map = {
        "collect": [],
        "clean": ["collect"],
        "extract": ["collect", "clean"],
        "knowledge": ["collect", "clean", "extract"],
        "inspect": ["collect", "clean", "extract", "knowledge"],
        "qgate": ["collect", "clean", "extract", "knowledge", "inspect"],
        "targeted": ["collect", "clean", "extract", "knowledge", "inspect", "qgate"],
        "merge": ["collect", "clean", "extract", "knowledge", "inspect", "qgate", "targeted"],
        "report": ["collect", "clean", "extract", "knowledge", "inspect", "qgate"],
    }
    for prec in preceding_map[crash_stage]:
        assert cs.is_stage_complete(st, prec), f"Preceding stage {prec} missing before crash at {crash_stage}"

    # Resume with resume=True and working execution
    pipe2, _ = _create_mock_pipeline(tmp_path, resume=True)
    res2 = await pipe2.run("challenger-empirical")

    assert res2.failed_stage is None
    assert "report" in res2.stages_completed or "report" in res2.stages_skipped

    # Verify deliverable summary
    summary_path = topic_dir / "run-summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0


@pytest.mark.asyncio
async def test_resume_preserves_artifact_bytes_and_mtimes_across_crash(tmp_path: pathlib.Path):
    """When a pipeline resumes, previously committed stages must NOT touch, rewrite, or modify artifact files."""
    pipe1, work_dir = _create_mock_pipeline(tmp_path, resume=False)
    orig_exec = pipe1._exec

    async def crash_at_report(stage: str, topic: str, topic_dir: pathlib.Path, result: Any):
        if stage == "report":
            raise RuntimeError("SIMULATED_CRASH_AT_STAGE_REPORT")
        await orig_exec(stage, topic, topic_dir, result)

    pipe1._exec = crash_at_report
    res1 = await pipe1.run("challenger-empirical")
    assert res1.failed_stage == "report"

    topic_dir = work_dir / "challenger-empirical"
    artifacts_dir = topic_dir / "artifacts"

    # Capture snapshot of bytes and mtimes before resume
    snapshot = {}
    for p in artifacts_dir.glob("*.json"):
        snapshot[p.name] = (p.read_bytes(), p.stat().st_mtime)

    # Ensure stages 1..8 exist
    for st in ("collect", "clean", "extract", "knowledge", "inspect", "qgate"):
        assert f"{st}.json" in snapshot or STAGE_ALIASES.get(st, "") + ".json" in snapshot

    # Pause briefly to ensure st_mtime would advance if files were rewritten
    time.sleep(0.05)

    # Resume
    pipe2, _ = _create_mock_pipeline(tmp_path, resume=True)
    res2 = await pipe2.run("challenger-empirical")

    assert res2.failed_stage is None
    assert "report" in res2.stages_completed

    # Invariant: Pre-existing artifacts have byte-identical content and untouched st_mtime
    for filename, (expected_bytes, expected_mtime) in snapshot.items():
        curr_file = artifacts_dir / filename
        assert curr_file.exists()
        assert curr_file.read_bytes() == expected_bytes, f"Artifact {filename} was modified on resume!"
        assert curr_file.stat().st_mtime == expected_mtime, f"Artifact {filename} mtime changed on resume!"


@pytest.mark.asyncio
async def test_crash_recovery_cleans_up_orphaned_tmp_files(tmp_path: pathlib.Path):
    """Abrupt crashes leave partial .tmp-* files; resume=True cleans them during load() with zero crash."""
    pipe, work_dir = _create_mock_pipeline(tmp_path, resume=True)
    topic_dir = work_dir / "challenger-empirical"
    topic_dir.mkdir(parents=True, exist_ok=True)
    art_dir = topic_dir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)

    # Scatter orphan tmp files
    (topic_dir / "state.json.tmp-worker1-ab12cd34").write_text("corrupt state", encoding="utf-8")
    (art_dir / "collect.json.tmp-worker2-ef56gh78").write_text("corrupt envelope", encoding="utf-8")
    (art_dir / "qgate.json.tmp-worker3-ij90kl12").write_text("corrupt gate", encoding="utf-8")

    res = await pipe.run("challenger-empirical")
    assert res.failed_stage is None

    # All orphan tmp files cleaned up
    assert list(topic_dir.glob("*.tmp-*")) == []
    assert list(art_dir.glob("*.tmp-*")) == []


@pytest.mark.asyncio
async def test_resume_rejects_mismatched_idempotency_key(tmp_path: pathlib.Path):
    """Resume execution fails fast if called with a different topic or mode against existing work dir."""
    pipe1, work_dir = _create_mock_pipeline(tmp_path, mode="full", resume=False)
    res1 = await pipe1.run("challenger-empirical")
    assert res1.failed_stage is None

    # Attempt resume with mode="brief"
    pipe2, _ = _create_mock_pipeline(tmp_path, mode="brief", resume=True)
    with pytest.raises(ChainStateFault) as exc_info:
        await pipe2.run("challenger-empirical")
    assert exc_info.value.code == E_IDEMPOTENCY_CONFLICT


# ============================================================================
# Dimension 5: Protocol v1 Envelope Compliance
# ============================================================================

@pytest.mark.asyncio
async def test_protocol_v1_envelope_invariants_across_all_nine_stages(tmp_path: pathlib.Path):
    """Assert all 9 stages produce envelopes strictly conforming to Protocol v1 schema invariants."""
    pipe, work_dir = _create_mock_pipeline(tmp_path)
    res = await pipe.run("challenger-empirical")
    assert res.failed_stage is None

    topic_dir = work_dir / "challenger-empirical"
    artifacts_dir = topic_dir / "artifacts"

    for stage in CANONICAL_NINE_STAGES:
        alias = STAGE_ALIASES.get(stage, stage)
        art_path = artifacts_dir / f"{stage}.json"
        if not art_path.exists():
            art_path = artifacts_dir / f"{alias}.json"
        assert art_path.exists(), f"Missing artifact for canonical stage {stage}"

        env = json.loads(art_path.read_text(encoding="utf-8"))

        # Invariant 1: Protocol version
        assert env.get("v") == 1, f"Stage {stage} envelope has invalid protocol version {env.get('v')}"

        # Invariant 2: Run ID non-empty
        assert isinstance(env.get("run_id"), str) and len(env["run_id"]) > 0

        # Invariant 3: Stage name canonical or accepted alias
        canon_set = {stage, alias, "gate" if stage == "qgate" else "", "network" if stage == "knowledge" else ""}
        assert env.get("stage") in canon_set, f"Unexpected stage field {env.get('stage')} in {stage}.json"

        # Invariant 4: Request ID non-empty
        assert isinstance(env.get("request_id"), str) and len(env["request_id"]) > 0

        # Invariant 5: Idempotency key is 64-hex
        idemp = env.get("idempotency_key")
        assert isinstance(idemp, str) and len(idemp) == 64
        int(idemp, 16)  # must parse as valid hex

        # Invariant 6: Budget lease non-negative and valid timestamps
        lease = env.get("budget_lease")
        assert isinstance(lease, dict)
        assert isinstance(lease.get("lease_id"), str)
        assert lease.get("tokens_max", -1) >= 0
        assert lease.get("cost_max", -1.0) >= 0.0
        assert lease.get("wall_s_max", -1.0) >= 0.0
        assert lease.get("search_calls_max", -1) >= 0
        assert "issued_at" in lease
        assert "expires_at" in lease

        # Invariant 7: Mutual exclusivity of result and error
        assert env.get("error") is None
        assert env.get("result") is not None


def test_protocol_v1_error_frame_structure_and_zero_result_leakage():
    """Verify that stage error envelopes have result=None and structured contract error frames."""
    graph = _make_sample_network(["src:01"])
    # Corrupt lease
    invalid_req = {
        "v": 1,
        "run_id": "run-err",
        "stage": "merge",
        "request_id": "req:err",
        "idempotency_key": "0" * 64,
        "budget_lease": {"invalid": True},
        "network_results": [{"v": 1, "stage": "network", "result": graph}],
        "responses": [{"request_id": "r1", "facts": [{"source_id": "s1", "locator": "l1", "content_sha256": "c1"}]}],
    }
    env = merge_min.run_merge(invalid_req)

    # Invariant: Zero payload leakage
    assert env["result"] is None
    # Invariant: Structured error frame
    err = env["error"]
    assert isinstance(err, dict)
    assert err["code"] == "E_LEASE_INVALID"
    assert err["stage"] == "merge"
    assert "safe_message" in err
    assert "retryable" in err
    assert isinstance(err["retryable"], bool)


# ============================================================================
# Dimension 6: Downstream Deliverables & SDK Entrypoint
# ============================================================================

@pytest.mark.asyncio
async def test_sdk_research_entrypoint_native_nine_stages(tmp_path: pathlib.Path, monkeypatch):
    """Verify SDK research() entrypoint executes native 9-stage pipeline and produces all deliverables."""
    work_dir = tmp_path / "sdk-work"

    # Patch ResearchPipeline methods so research() executes offline with mock LLM and mock exec
    mock_llm = MockLLMClient(
        chat_response="# 报告\n\nSDK实证核验完成 (来源01)。\n\n## 参考资料\n- 来源01：SDK — https://arxiv.org/abs/sdk-topic"
    )
    monkeypatch.setattr(ResearchPipeline, "_get_llm", lambda self: mock_llm)

    async def mock_exec(self, stage: str, topic: str, topic_dir: pathlib.Path, result: PipelineResult):
        if stage == "collect":
            raw = topic_dir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "paper.md").write_text(
                "<!-- source: https://arxiv.org/abs/sdk-topic -->\n"
                "<!-- fetched: 2026-09-17T14:00:00Z -->\nSDK内容",
                encoding="utf-8",
            )
            sources = [
                {
                    "url": "https://arxiv.org/abs/sdk-topic",
                    "title": "SDK",
                    "fetchedAt": "2026-09-17T14:00:00Z",
                    "content_hash": "sha256:" + "e" * 64,
                }
            ]
            (raw / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        elif stage == "clean":
            clean = topic_dir / "clean"
            clean.mkdir(parents=True, exist_ok=True)
            (clean / "paper.md").write_text("清洗后SDK内容", encoding="utf-8")
        elif stage == "extract":
            extracted = topic_dir / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            (extracted / "entities.json").write_text('[]', encoding="utf-8")
        elif stage in ("organize", "knowledge"):
            tree = topic_dir / "tree"
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "00-主表.md").write_text("# 知识网络大纲\n\n- [[N01-SDK|SDK概述]]\n", encoding="utf-8")
            (tree / "N01-SDK.md").write_text("<!-- source: https://arxiv.org/abs/sdk-topic -->\n# SDK\n内容", encoding="utf-8")
        elif stage == "report":
            pass

    monkeypatch.setattr(ResearchPipeline, "_exec", mock_exec)

    res = await research("sdk-topic", work_dir=work_dir)
    assert res.failed_stage is None

    topic_dir = work_dir / "sdk-topic"
    assert (topic_dir / "report.md").exists()
    assert (topic_dir / "tree" / "00-主表.md").exists()
    assert (topic_dir / "sources.json").exists()
    assert (topic_dir / "run-summary.json").exists()

    summary = json.loads((topic_dir / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["pipeline_complete"] is True
    assert summary["citation_coverage"] == 1.0

    # Verify that all 9 stage envelopes were committed to artifacts/
    artifacts = topic_dir / "artifacts"
    for st in CANONICAL_NINE_STAGES:
        assert (artifacts / f"{st}.json").exists() or (artifacts / f"{STAGE_ALIASES.get(st, '')}.json").exists()


def test_load_config_overrides_preservation_under_full_mode():
    """Verify load_config() strictly preserves caller overrides for stages under mode='full'."""
    custom_stages = ["collect", "clean", "extract", "organize", "report"]
    cfg = load_config(overrides={"mode": "full", "stages": custom_stages})
    assert cfg.stages == custom_stages
    assert "deepen" not in cfg.stages
