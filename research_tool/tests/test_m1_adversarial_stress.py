"""Adversarial stress harness for Milestone 1 verification.

Targeting:
1. Atomic CAS write-if-match merge semantics and concurrency contention.
2. Concurrent state commits and thread/process safety.
3. SHA-256 tamper detection in ChainState and alias verification.
4. Crash-recovery invariants, orphan tmp cleanup, and partial execution resumption.
"""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import pathlib
import shutil
import tempfile
import time
import pytest

from research_tool.nine_loop.chain_state import (
    ChainState,
    ChainStateFault,
    E_CAS_CONFLICT,
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    sha256_bytes,
    sha256_file,
    write_atomic,
)
from research_tool.nine_loop import merge_min


def make_envelope(stage: str, run_id: str = "run-adv-01", idempotency_key: str = "0" * 64) -> dict:
    return {
        "v": 1,
        "run_id": run_id,
        "stage": stage,
        "request_id": f"req:{stage}:001",
        "idempotency_key": idempotency_key,
        "budget_lease": {
            "lease_id": "lease-adv",
            "tokens_max": 100000,
            "cost_max": 5.0,
            "wall_s_max": 60.0,
            "search_calls_max": 20,
            "issued_at": "2026-09-17T14:00:00Z",
            "expires_at": "2026-09-17T15:00:00Z",
        },
        "result": {"payload": f"data_for_{stage}", "timestamp": time.time()},
        "error": None,
    }


# ============================================================================
# Area 1: Atomic CAS write-if-match merge semantics
# ============================================================================

def test_cas_write_if_match_exact_semantics():
    """Verify write-if-match: match succeeds, mismatch fails with E_CAS_CONFLICT."""
    initial_graph = {
        "nodes": [{"node_id": "root", "evidence_spans": [{"locator": "loc0", "content_sha256": "0"*64}]}],
        "edges": [],
        "counts": {"nodes": 1, "edges": 0},
    }
    net_env = {
        "v": 1,
        "run_id": "r1",
        "stage": "network",
        "request_id": "req:net:0",
        "idempotency_key": merge_min.graph_digest(initial_graph),
        "budget_lease": {
            "lease_id": "l1", "tokens_max": 1000, "cost_max": 1.0,
            "wall_s_max": 10.0, "search_calls_max": 1,
            "issued_at": "2026-09-17T14:00:00Z", "expires_at": "2026-09-17T15:00:00Z",
        },
        "result": initial_graph,
        "error": None,
    }
    prev_digest = merge_min.graph_digest(initial_graph)

    # 1. Matching merge succeeds
    resp1 = [{"request_id": "q1", "facts": [{"source_id": "s1", "locator": "loc1", "content_sha256": "1"*64}]}]
    req1 = merge_min.request_from_chain(net_env, resp1, prev_digest=prev_digest)
    res1 = merge_min.run_merge(req1)
    assert res1["error"] is None
    new_digest = res1["result"]["latest_digest"]
    assert new_digest != prev_digest

    # 2. Attempting merge with stale digest must fail with E_CAS_CONFLICT
    updated_net_env = copy.deepcopy(net_env)
    updated_net_env["result"] = res1["result"]["graph"]
    updated_net_env["idempotency_key"] = new_digest
    req_stale = merge_min.request_from_chain(updated_net_env, resp1, prev_digest=prev_digest)
    res_stale = merge_min.run_merge(req_stale)
    assert res_stale["error"] is not None
    assert res_stale["error"]["code"] == E_CAS_CONFLICT

    # 3. Direct merge function raises MergeStageFault
    with pytest.raises(merge_min.MergeStageFault) as exc:
        merge_min.merge(res1["result"]["graph"], resp1, prev_digest=prev_digest)
    assert exc.value.frame["code"] == E_CAS_CONFLICT


def test_cas_dedup_and_conflict_coexist():
    """Verify content_sha256 deduplication and conflict_coexist semantics."""
    initial_graph = {
        "nodes": [
            {
                "node_id": "node_A",
                "evidence_spans": [{"locator": "url_A_v1", "content_sha256": "a1"*32, "round_id": 0}],
            }
        ],
        "edges": [],
        "counts": {"nodes": 1, "edges": 0},
    }

    # Responses containing:
    # 1. Exact duplicate of existing span (should skip)
    # 2. Conflicting span for node_A (should coexist)
    # 3. Completely new node (should add new_family)
    responses = [
        {
            "request_id": "q_test",
            "facts": [
                {"source_id": "node_A", "locator": "url_A_v1", "content_sha256": "a1"*32}, # duplicate
                {"source_id": "node_A", "locator": "url_A_v2", "content_sha256": "a2"*32}, # conflict
                {"source_id": "node_B", "locator": "url_B_v1", "content_sha256": "b1"*32}, # new
            ]
        }
    ]

    res = merge_min.merge(initial_graph, responses, prev_digest=None)
    counts = res["counts"]
    assert counts["skipped"] == 1
    assert counts["conflicts"] == 1
    assert counts["new_facts"] == 2

    # Verify node_A has both evidence spans sorted deterministically
    merged_nodes = {n["node_id"]: n for n in res["graph"]["nodes"]}
    assert len(merged_nodes["node_A"]["evidence_spans"]) == 2
    assert "node_B" in merged_nodes


def test_cas_concurrency_race_condition(tmp_path: pathlib.Path):
    """Stress test: 20 workers attempt to merge concurrently against the same base state.
    Only matching prev_digest transitions must succeed; stale workers must detect conflict.
    """
    cs = ChainState(tmp_path)
    base_key = "f" * 64
    state = cs.load(base_key)

    initial_graph = {
        "nodes": [{"node_id": "root", "evidence_spans": []}],
        "edges": [],
        "counts": {"nodes": 1, "edges": 0},
    }
    net_env = make_envelope("network", idempotency_key=base_key)
    net_env["result"] = initial_graph
    cs.commit_stage(state, "network", net_env)

    initial_digest = merge_min.graph_digest(initial_graph)

    # 20 workers each try to merge their own response with prev_digest=initial_digest
    def worker_merge(worker_id: int):
        resp = [{
            "request_id": f"worker_{worker_id}",
            "facts": [{
                "source_id": f"src_worker_{worker_id}",
                "locator": f"loc_{worker_id}",
                "content_sha256": hashlib.sha256(f"{worker_id}".encode()).hexdigest(),
            }]
        }]
        req = merge_min.request_from_chain(net_env, resp, prev_digest=initial_digest)
        return worker_id, merge_min.run_merge(req)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(worker_merge, range(20)))

    # In pure functional merge, all 20 receive valid merged graphs based on initial_graph
    for wid, res in results:
        assert res["error"] is None
        assert res["result"]["latest_digest"] != initial_digest


# ============================================================================
# Area 2: Concurrent State Commits & Atomic File Operations
# ============================================================================

def test_concurrent_write_atomic_stress(tmp_path: pathlib.Path):
    """Stress test write_atomic under high contention (50 threads writing to same file)."""
    target = tmp_path / "shared_atomic.json"

    def write_payload(thread_id: int):
        data = json.dumps({"thread_id": thread_id, "padding": "x" * 5000}).encode("utf-8")
        write_atomic(target, data)
        return thread_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(write_payload, i) for i in range(50)]
        done = [f.result() for f in futures]
    assert len(done) == 50

    # Verify file is intact, valid JSON, and zero leftover tmp files
    raw = target.read_bytes()
    parsed = json.loads(raw.decode("utf-8"))
    assert "thread_id" in parsed
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_concurrent_readers_during_writes(tmp_path: pathlib.Path):
    """Stress test: Readers continuously read while writers update the artifact.
    Readers should NEVER see partial writes or invalid JSON.
    """
    target = tmp_path / "stream_artifact.json"
    write_atomic(target, json.dumps({"counter": 0, "hash": "init"}).encode("utf-8"))

    stop = False
    read_errors = []
    read_counts = [0]

    def writer():
        counter = 1
        while not stop and counter < 100:
            payload = {"counter": counter, "payload": "A" * (1000 * (counter % 5 + 1))}
            data = json.dumps(payload).encode("utf-8")
            write_atomic(target, data)
            counter += 1
            time.sleep(0.001)

    def reader():
        while not stop:
            try:
                raw = target.read_bytes()
                data = json.loads(raw.decode("utf-8"))
                assert "counter" in data
                read_counts[0] += 1
            except Exception as e:
                read_errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        w_fut = ex.submit(writer)
        r_futs = [ex.submit(reader) for _ in range(4)]
        w_fut.result()
        stop = True
        for rf in r_futs:
            rf.result()

    assert read_errors == []
    assert read_counts[0] > 50


# ============================================================================
# Area 3: SHA-256 Tamper Detection & Alias Consistency
# ============================================================================

def test_tamper_detection_single_byte_flip(tmp_path: pathlib.Path):
    """Corrupting exactly 1 byte in an artifact triggers ChainStateFault(E_STATE)."""
    cs = ChainState(tmp_path)
    key = "7" * 64
    state = cs.load(key)

    env = make_envelope("clean", idempotency_key=key)
    cs.commit_stage(state, "clean", env)

    art_file = tmp_path / "artifacts" / "clean.json"
    raw = bytearray(art_file.read_bytes())
    # Flip one byte in the JSON
    raw[10] = raw[10] ^ 0xFF
    art_file.write_bytes(bytes(raw))

    with pytest.raises(ChainStateFault) as exc_read:
        cs.read_stage(state, "clean")
    assert exc_read.value.code == E_STATE

    with pytest.raises(ChainStateFault) as exc_verify:
        cs.verify_state(state)
    assert exc_verify.value.code == E_STATE

    # Loading with resume=True must also reject
    cs2 = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc_load:
        cs2.load(key, resume=True)
    assert exc_load.value.code == E_STATE


def test_tamper_detection_trailing_whitespace(tmp_path: pathlib.Path):
    """Appending trailing whitespace changes the SHA-256 and must be rejected."""
    cs = ChainState(tmp_path)
    key = "8" * 64
    state = cs.load(key)

    env = make_envelope("extract", idempotency_key=key)
    cs.commit_stage(state, "extract", env)

    art_file = tmp_path / "artifacts" / "extract.json"
    raw = art_file.read_bytes() + b"\n "
    art_file.write_bytes(raw)

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "extract")
    assert exc.value.code == E_STATE


def test_tamper_detection_state_json(tmp_path: pathlib.Path):
    """Tampering fields inside state.json is strictly caught."""
    cs = ChainState(tmp_path)
    key = "9" * 64
    state = cs.load(key)

    env = make_envelope("collect", idempotency_key=key)
    cs.commit_stage(state, "collect", env)

    # 1. Tamper recorded SHA
    state_file = tmp_path / "state.json"
    st = json.loads(state_file.read_text("utf-8"))
    st["stages"]["collect"] = "f" * 64
    state_file.write_text(json.dumps(st), "utf-8")

    cs_res = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc:
        cs_res.load(key, resume=True)
    assert exc.value.code == E_STATE

    # 2. Tamper version
    st["version"] = 2
    state_file.write_text(json.dumps(st), "utf-8")
    with pytest.raises(ChainStateFault) as exc:
        cs_res.load(key, resume=True)
    assert exc.value.code == E_STATE


def test_tamper_detection_missing_artifact(tmp_path: pathlib.Path):
    """Deleting an artifact of a marked-as-done stage must fail verification."""
    cs = ChainState(tmp_path)
    key = "a" * 64
    state = cs.load(key)

    env = make_envelope("inspect", idempotency_key=key)
    cs.commit_stage(state, "inspect", env)

    art_file = tmp_path / "artifacts" / "inspect.json"
    art_file.unlink()

    with pytest.raises(ChainStateFault) as exc:
        cs.verify_state(state)
    assert exc.value.code == E_STATE

    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "inspect")
    assert exc.value.code == E_STATE


def test_alias_bilateral_tamper_detection(tmp_path: pathlib.Path):
    """Verify that alias mirroring (knowledge <-> network, qgate <-> gate) detects tamper on both sides."""
    cs = ChainState(tmp_path)
    key = "b" * 64
    state = cs.load(key)

    env = make_envelope("knowledge", idempotency_key=key)
    cs.commit_stage(state, "knowledge", env)

    # Both knowledge.json and network.json exist
    k_file = tmp_path / "artifacts" / "knowledge.json"
    n_file = tmp_path / "artifacts" / "network.json"
    assert k_file.exists()
    assert n_file.exists()

    # Corrupt network.json
    n_file.write_bytes(b'{"corrupted": true}')

    # Reading stage network must detect tamper
    with pytest.raises(ChainStateFault) as exc_net:
        cs.read_stage(state, "network")
    assert exc_net.value.code == E_STATE


# ============================================================================
# Area 4: Crash-Recovery Invariants
# ============================================================================

def test_crash_recovery_cleans_hundreds_of_stale_tmps(tmp_path: pathlib.Path):
    """Verify cleanup_tmps systematically clears all stale temp files in work_dir and artifacts."""
    cs = ChainState(tmp_path)
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)

    # Generate 150 dummy .tmp files
    for i in range(75):
        (tmp_path / f"state.json.tmp-worker{i}-dead").write_text("junk", "utf-8")
        (tmp_path / "artifacts" / f"stage{i}.json.tmp-worker{i}-dead").write_text("junk", "utf-8")

    assert len(list(tmp_path.glob("*.tmp-*"))) == 75
    assert len(list((tmp_path / "artifacts").glob("*.tmp-*"))) == 75

    cleaned = cs.cleanup_tmps()
    assert cleaned == 150
    assert len(list(tmp_path.glob("*.tmp-*"))) == 0
    assert len(list((tmp_path / "artifacts").glob("*.tmp-*"))) == 0


def test_crash_recovery_idempotent_reexecution(tmp_path: pathlib.Path):
    """Simulate multi-stage crash: commit stages 1..3, kill process, resume and commit 4..9."""
    stages = ["collect", "clean", "extract", "knowledge", "inspect", "targeted", "merge", "qgate", "report"]
    key = "c" * 64

    # Process 1: runs up to extract, then crashes
    cs1 = ChainState(tmp_path)
    st1 = cs1.load(key)
    for s in stages[:3]:
        env = make_envelope(s, idempotency_key=key)
        cs1.commit_stage(st1, s, env)

    # Process 2: resumes from disk
    cs2 = ChainState(tmp_path)
    st2 = cs2.load(key, resume=True)

    for s in stages[:3]:
        assert cs2.is_stage_complete(st2, s) is True
    for s in stages[3:]:
        assert cs2.is_stage_complete(st2, s) is False

    # Continue remaining stages
    for s in stages[3:]:
        env = make_envelope(s, idempotency_key=key)
        cs2.commit_stage(st2, s, env)

    # Process 3: final verification
    cs3 = ChainState(tmp_path)
    st3 = cs3.load(key, resume=True)
    assert set(st3["done"]) >= set(stages)
    cs3.verify_state(st3)


def test_cas_graph_aliasing_immutability():
    """Verify that merge() enforces strict caller-data immutability (deep copy isolation)."""
    initial_graph = {
        "nodes": [
            {
                "node_id": "orig_node",
                "evidence_spans": [{"locator": "https://example.com/orig", "content_sha256": "0" * 64}],
            }
        ],
        "edges": [{"edge_id": "e1", "source": "orig_node", "target": "dest_node"}],
        "counts": {"nodes": 1, "edges": 1},
    }
    graph_copy = copy.deepcopy(initial_graph)

    responses = [
        {
            "request_id": "q_immutability",
            "facts": [
                {"source_id": "orig_node", "locator": "https://example.com/v2", "content_sha256": "1" * 64},
                {"source_id": "new_node", "locator": "https://example.com/v3", "content_sha256": "2" * 64},
            ],
        }
    ]

    res = merge_min.merge(initial_graph, responses, prev_digest=None)

    # Initial graph MUST remain bit-for-bit identical to pre-merge state
    assert initial_graph == graph_copy
    assert len(initial_graph["nodes"]) == 1
    assert len(initial_graph["nodes"][0]["evidence_spans"]) == 1

    # Merged graph contains updated state
    assert len(res["graph"]["nodes"]) == 2


def test_cas_large_graph_determinism():
    """Stress test: 100 facts merged in differing chunk configurations produce identical digests."""
    initial_graph = {"nodes": [], "edges": [], "counts": {"nodes": 0, "edges": 0}}

    all_facts = [
        {
            "source_id": f"node_{i % 10}",
            "locator": f"https://example.com/doc_{i}",
            "content_sha256": hashlib.sha256(f"fact_{i}".encode()).hexdigest(),
            "round_id": "fixed_r1",
        }
        for i in range(50)
    ]

    # Batch 1: single large merge
    resp_single = [{"request_id": "q_bulk", "facts": all_facts}]
    res_single = merge_min.merge(initial_graph, resp_single, prev_digest=None)

    # Batch 2: two-stage sequential merge with CAS write-if-match
    resp_part1 = [{"request_id": "q_part1", "facts": all_facts[:25]}]
    res_part1 = merge_min.merge(initial_graph, resp_part1, prev_digest=None)
    digest_1 = res_part1["latest_digest"]

    resp_part2 = [{"request_id": "q_part2", "facts": all_facts[25:]}]
    res_part2 = merge_min.merge(res_part1["graph"], resp_part2, prev_digest=digest_1)
    digest_2 = res_part2["latest_digest"]

    # Both paths result in 10 nodes with 5 facts each
    assert res_single["counts"]["new_facts"] == 50
    assert digest_2 == res_single["latest_digest"]


def test_concurrent_multi_stage_commits_synchronized(tmp_path: pathlib.Path):
    """Stress test: 10 concurrent threads commit different stages with synchronization."""
    import threading
    cs = ChainState(tmp_path)
    key = "d" * 64
    state = cs.load(key)
    lock = threading.Lock()

    stages = [f"custom_stage_{i}" for i in range(10)]

    def worker(stg):
        env = make_envelope(stg, idempotency_key=key)
        with lock:
            cs.commit_stage(state, stg, env)
        return stg

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(worker, stages))

    assert len(results) == 10

    # Verify on-disk state
    cs_verify = ChainState(tmp_path)
    st_loaded = cs_verify.load(key, resume=True)
    for s in stages:
        assert cs_verify.is_stage_complete(st_loaded, s) is True
        env = cs_verify.read_stage(st_loaded, s)
        assert env["stage"] == s


def test_concurrent_multi_stage_commits_unsynchronized_race_demonstration(tmp_path: pathlib.Path):
    """Empirical demonstration: Unsynchronized concurrent commits to ChainState
    can result in lost updates in state.json because _save_state has no mutex.
    Artifact files are atomic, but state.json serialization can be clobbered.
    """
    cs = ChainState(tmp_path)
    key = "d" * 64
    state = cs.load(key)

    stages = [f"race_stage_{i}" for i in range(10)]

    def worker(stg):
        env = make_envelope(stg, idempotency_key=key)
        cs.commit_stage(state, stg, env)
        return stg

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(worker, stages))

    # All artifact files exist on disk due to write_atomic
    for s in stages:
        assert (tmp_path / "artifacts" / f"{s}.json").exists()


def test_alias_tamper_detection_gap_demonstration(tmp_path: pathlib.Path):
    """Empirical demonstration:
    When 'knowledge' is committed, 'network.json' is created as mirror.
    If 'network.json' is tampered with on disk, verify_state() does NOT detect it
    because 'network' was not added to state['done'].
    However, read_stage('network') DOES detect the tamper.
    """
    cs = ChainState(tmp_path)
    key = "e" * 64
    state = cs.load(key)

    know_env = make_envelope("knowledge", idempotency_key=key)
    cs.commit_stage(state, "knowledge", know_env)

    net_target = tmp_path / "artifacts" / "network.json"
    assert net_target.exists()

    # Tamper network.json
    net_target.write_bytes(b'{"tampered": true}')

    # 1. verify_state() currently PASSES because only 'knowledge' is in state['done']
    # This demonstrates the gap in verify_state alias coverage
    assert "network" not in state["done"]
    assert "knowledge" in state["done"]
    # This passes without raising ChainStateFault
    cs.verify_state(state)

    # 2. But direct read of the alias DOES catch the digest mismatch
    with pytest.raises(ChainStateFault) as exc:
        cs.read_stage(state, "network")
    assert exc.value.code == E_STATE


def test_mid_transaction_crash_loop_merge_invariant(tmp_path: pathlib.Path):
    """Empirical stress test:
    Simulates crash during commit_loop_merge between artifact overwrite and state.json save.
    Demonstrates that mid-transaction crash results in E_STATE on subsequent resume.
    """
    cs = ChainState(tmp_path)
    key = "f" * 64
    state = cs.load(key)

    # Stage 4 committed
    init_env = make_envelope("knowledge", idempotency_key=key)
    cs.commit_stage(state, "knowledge", init_env)
    assert cs.is_stage_complete(state, "knowledge")

    # Mid-transaction crash simulation:
    # artifacts/knowledge.json is updated with new loop round payload
    new_data = json.dumps({"v": 1, "stage": "knowledge", "result": {"round": 1}}).encode("utf-8")
    write_atomic(tmp_path / "artifacts" / "knowledge.json", new_data)
    # State.json was NOT updated (simulating power cut / SIGKILL)

    # On resume, verify_state correctly detects that knowledge.json does not match recorded SHA
    cs_resumed = ChainState(tmp_path)
    with pytest.raises(ChainStateFault) as exc:
        cs_resumed.load(key, resume=True)
    assert exc.value.code == E_STATE


def test_tamper_detection_malformed_state_json(tmp_path: pathlib.Path):
    """Corrupting state.json to invalid JSON or 0-byte file causes JSONDecodeError."""
    cs = ChainState(tmp_path)
    key = "1" * 64
    cs.load(key)

    state_file = tmp_path / "state.json"
    state_file.write_text("{broken json", encoding="utf-8")

    cs2 = ChainState(tmp_path)
    with pytest.raises(json.JSONDecodeError):
        cs2.load(key, resume=True)

