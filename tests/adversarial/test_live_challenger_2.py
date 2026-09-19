"""Empirical Challenger 2 Live Verification & Adversarial Stress Test Suite.

Empirically challenges:
1. ChainState atomic commit integrity: SHA-256 cryptographic hashes & stage sequence invariants
2. Resume behavior: idempotency and tamper detection on live output directories
3. Citation Coverage 1.0 enforcement: physical exclusion of unverified/invalid claims
4. Wiki-stage packaging compliance on live artifacts
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

from research_tool.infrastructure.export import wiki_stage
from research_tool.nine_loop import report_min
from research_tool.nine_loop.chain_state import (
    E_IDEMPOTENCY_CONFLICT,
    E_STATE,
    ChainState,
    ChainStateFault,
    sha256_bytes,
)

LIVE_FULL_DIR = pathlib.Path("research-output/liang-zi-ji-suan-qian-yan-jin-zhan")
LIVE_BRIEF_DIR = pathlib.Path("research-output/fen-bu-shi-gong-shi-suan-fa")


class TestLiveArtifactCommitIntegrity(unittest.TestCase):
    """Verifies SHA-256 cryptographic hashes and stage sequence invariants on live outputs."""

    def test_live_full_mode_sha256_hashes(self) -> None:
        """Every artifact in live 9-stage output must match the SHA-256 recorded in state.json."""
        self.assertTrue(LIVE_FULL_DIR.exists(), f"Missing live directory: {LIVE_FULL_DIR}")
        state_path = LIVE_FULL_DIR / "state.json"
        self.assertTrue(state_path.exists(), "Missing state.json")

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state.get("version"), 1)

        expected_key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
        self.assertEqual(state.get("input_idempotency_key"), expected_key)

        artifacts_dir = LIVE_FULL_DIR / "artifacts"
        self.assertTrue(artifacts_dir.exists(), "Missing artifacts directory")

        expected_stages = [
            "collect",
            "clean",
            "extract",
            "knowledge",
            "inspect",
            "qgate",
            "targeted",
            "merge",
            "report",
        ]
        done_stages = state.get("done", [])
        for stage in expected_stages:
            self.assertIn(stage, done_stages, f"Stage {stage} not marked done in state.json")

        stages_dict = state.get("stages", {})
        artifact_files = sorted(artifacts_dir.glob("*.json"))
        self.assertGreaterEqual(len(artifact_files), 9)

        for fpath in artifact_files:
            stage_name = fpath.stem
            content = fpath.read_bytes()
            computed_sha = sha256_bytes(content)
            recorded_sha = stages_dict.get(stage_name)
            self.assertIsNotNone(
                recorded_sha, f"Artifact {fpath.name} has no hash recorded in state.json"
            )
            self.assertEqual(
                computed_sha,
                recorded_sha,
                f"Cryptographic hash mismatch for {fpath.name}: computed {computed_sha}, recorded {recorded_sha}",
            )

    def test_live_brief_mode_sha256_hashes(self) -> None:
        """Every artifact in live brief mode output must match the SHA-256 recorded in state.json."""
        self.assertTrue(LIVE_BRIEF_DIR.exists(), f"Missing live directory: {LIVE_BRIEF_DIR}")
        state_path = LIVE_BRIEF_DIR / "state.json"
        self.assertTrue(state_path.exists(), "Missing state.json in brief output")

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state.get("version"), 1)
        expected_key = hashlib.sha256("分布式共识算法:brief".encode()).hexdigest()
        self.assertEqual(state.get("input_idempotency_key"), expected_key)

        done_stages = state.get("done", [])
        for stage in ["collect", "clean", "report"]:
            self.assertIn(stage, done_stages)

        artifacts_dir = LIVE_BRIEF_DIR / "artifacts"
        stages_dict = state.get("stages", {})
        for fpath in sorted(artifacts_dir.glob("*.json")):
            computed_sha = sha256_bytes(fpath.read_bytes())
            recorded_sha = stages_dict.get(fpath.stem)
            self.assertEqual(computed_sha, recorded_sha)

    def test_canonical_alias_mirror_identity(self) -> None:
        """Verifies knowledge/network and qgate/gate are byte-identical and hash-identical."""
        artifacts_dir = LIVE_FULL_DIR / "artifacts"
        # knowledge vs network
        kn_bytes = (artifacts_dir / "knowledge.json").read_bytes()
        net_bytes = (artifacts_dir / "network.json").read_bytes()
        self.assertEqual(
            kn_bytes, net_bytes, "knowledge.json and network.json must be byte-identical"
        )

        # qgate vs gate
        qgate_bytes = (artifacts_dir / "qgate.json").read_bytes()
        gate_bytes = (artifacts_dir / "gate.json").read_bytes()
        self.assertEqual(qgate_bytes, gate_bytes, "qgate.json and gate.json must be byte-identical")

    def test_envelope_v1_protocol_schema(self) -> None:
        """Every artifact envelope in live output must strictly satisfy Protocol v1."""
        artifacts_dir = LIVE_FULL_DIR / "artifacts"
        required_keys = {
            "v",
            "run_id",
            "stage",
            "request_id",
            "idempotency_key",
            "budget_lease",
            "result",
            "error",
        }
        for fpath in artifacts_dir.glob("*.json"):
            data = json.loads(fpath.read_text(encoding="utf-8"))
            self.assertEqual(data.get("v"), 1, f"{fpath.name}: version must be 1")
            self.assertIsNone(data.get("error"), f"{fpath.name}: error must be null")
            self.assertTrue(data.get("run_id"), f"{fpath.name}: missing run_id")
            self.assertIsInstance(data.get("budget_lease"), dict, f"{fpath.name}: invalid lease")
            self.assertSetEqual(required_keys, set(data.keys()), f"{fpath.name}: key mismatch")


class TestLiveResumeAndTamperDetection(unittest.TestCase):
    """Adversarially tests --resume idempotency and tamper detection."""

    def test_live_resume_idempotent_load(self) -> None:
        """ChainState.load on the untouched live output directory must succeed with verify_state."""
        cs = ChainState(LIVE_FULL_DIR)
        key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
        state = cs.load(key, resume=True)
        self.assertIsInstance(state, dict)
        self.assertEqual(len(state["done"]), 9)
        # Check all stages complete
        for s in [
            "collect",
            "clean",
            "extract",
            "knowledge",
            "inspect",
            "qgate",
            "targeted",
            "merge",
            "report",
        ]:
            self.assertTrue(cs.is_stage_complete(state, s))

    def test_tamper_detection_modified_byte_in_artifact(self) -> None:
        """Tampering with a single byte in any stage artifact must raise E_STATE on resume."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_work = pathlib.Path(tmp) / "tampered"
            shutil.copytree(LIVE_FULL_DIR, tmp_work)

            # Tamper 1 byte in extract.json
            extract_path = tmp_work / "artifacts" / "extract.json"
            raw = extract_path.read_bytes()
            # Replace character
            tampered_raw = raw.replace(b"extract", b"extracX")
            extract_path.write_bytes(tampered_raw)

            cs = ChainState(tmp_work)
            key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
            with self.assertRaises(ChainStateFault) as cm:
                cs.load(key, resume=True)
            self.assertEqual(cm.exception.code, E_STATE)
            self.assertIn("artifact digest mismatch for extract", str(cm.exception))

    def test_tamper_detection_missing_artifact(self) -> None:
        """Removing a completed stage artifact must raise E_STATE on resume."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_work = pathlib.Path(tmp) / "tampered"
            shutil.copytree(LIVE_FULL_DIR, tmp_work)

            # Remove clean.json
            clean_path = tmp_work / "artifacts" / "clean.json"
            clean_path.unlink()

            cs = ChainState(tmp_work)
            key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
            with self.assertRaises(ChainStateFault) as cm:
                cs.load(key, resume=True)
            self.assertEqual(cm.exception.code, E_STATE)
            self.assertIn("missing artifact for completed stage clean", str(cm.exception))

    def test_tamper_detection_idempotency_key_mismatch(self) -> None:
        """Resuming with a mismatched input key (different topic/mode) must raise E_IDEMPOTENCY_CONFLICT."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_work = pathlib.Path(tmp) / "tampered"
            shutil.copytree(LIVE_FULL_DIR, tmp_work)

            cs = ChainState(tmp_work)
            wrong_key = hashlib.sha256("量子计算前沿进展:brief".encode()).hexdigest()
            with self.assertRaises(ChainStateFault) as cm:
                cs.load(wrong_key, resume=True)
            self.assertEqual(cm.exception.code, E_IDEMPOTENCY_CONFLICT)

    def test_tamper_detection_unsupported_version(self) -> None:
        """Corrupting state.json version must raise E_STATE on load."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_work = pathlib.Path(tmp) / "tampered"
            shutil.copytree(LIVE_FULL_DIR, tmp_work)

            state_file = tmp_work / "state.json"
            st = json.loads(state_file.read_text(encoding="utf-8"))
            st["version"] = 999
            state_file.write_text(json.dumps(st), encoding="utf-8")

            cs = ChainState(tmp_work)
            key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
            with self.assertRaises(ChainStateFault) as cm:
                cs.load(key, resume=True)
            self.assertEqual(cm.exception.code, E_STATE)
            self.assertIn("unsupported state version", str(cm.exception))

    def test_tmp_cleanup_removes_orphaned_temporary_files(self) -> None:
        """ChainState.cleanup_tmps must cleanly purge stale .tmp-* files without touching valid artifacts."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_work = pathlib.Path(tmp) / "tampered"
            shutil.copytree(LIVE_FULL_DIR, tmp_work)

            # Inject stale temp files
            tmp_file1 = tmp_work / "artifacts" / "collect.json.tmp-1234-abcd"
            tmp_file2 = tmp_work / "state.json.tmp-5678-ef01"
            tmp_file1.write_bytes(b"partial junk")
            tmp_file2.write_bytes(b"partial state junk")

            cs = ChainState(tmp_work)
            cleaned = cs.cleanup_tmps()
            self.assertEqual(cleaned, 2)
            self.assertFalse(tmp_file1.exists())
            self.assertFalse(tmp_file2.exists())
            # Real artifacts intact
            key = hashlib.sha256("量子计算前沿进展:full".encode()).hexdigest()
            state = cs.load(key, resume=True)
            self.assertIsNotNone(state)


class TestCitationCoverageEnforcement(unittest.TestCase):
    """Adversarially tests Citation Coverage 1.0 enforcement and physical claim exclusion."""

    def test_physical_exclusion_of_claims_without_citations(self) -> None:
        """Claims derived from nodes without evidence spans MUST be dropped and never included in report."""
        network_env = {
            "v": 1,
            "stage": "network",
            "run_id": "run-test-01",
            "request_id": "req-01",
            "idempotency_key": "a" * 64,
            "budget_lease": {
                "lease_id": "l-1",
                "tokens_max": 1000,
                "cost_max": 1.0,
                "wall_s_max": 10.0,
                "search_calls_max": 5,
                "issued_at": "2026-09-18T10:00:00Z",
                "expires_at": "2026-09-18T10:10:00Z",
            },
            "result": {
                "nodes": [
                    {
                        "node_id": "node-verified",
                        "title": "Verified Fact",
                        "evidence_spans": [
                            {
                                "source_id": "src:1",
                                "locator": "https://arxiv.org/abs/verified",
                                "content_sha256": "hash1",
                            }
                        ],
                    },
                    {
                        "node_id": "node-unverified",
                        "title": "Unverified Hallucination",
                        "evidence_spans": [],  # Zero citations
                    },
                ]
            },
            "error": None,
        }

        claims, dropped = report_min.build_claims([network_env])
        # Only verified claim retained
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_id"], "claim:node-verified")
        self.assertEqual(len(claims[0]["citations"]), 1)

        # Unverified node physically dropped
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["claim_id"], "claim:node-unverified")
        self.assertEqual(dropped[0]["reason"], "no_resolvable_citation")

    def test_report_min_run_report_verdict_routing(self) -> None:
        """Verifies report_min.run_report respects gate verdict routing and contract rules."""
        lease = {
            "lease_id": "l-1",
            "tokens_max": 1000,
            "cost_max": 1.0,
            "wall_s_max": 10.0,
            "search_calls_max": 5,
            "issued_at": "2026-09-18T10:00:00Z",
            "expires_at": "2026-09-18T10:10:00Z",
        }
        net_env = {
            "v": 1,
            "stage": "network",
            "run_id": "run-test-01",
            "request_id": "net:01",
            "idempotency_key": "b" * 64,
            "budget_lease": lease,
            "result": {
                "nodes": [
                    {
                        "node_id": "node-1",
                        "evidence_spans": [
                            {"source_id": "s1", "locator": "http://example.com/1", "content_sha256": "h1"}
                        ],
                    }
                ]
            },
            "error": None,
        }

        # Case 1: CONTINUE verdict -> report is None (routed back to targeted)
        gate_continue = {
            "v": 1,
            "stage": "gate",
            "run_id": "run-test-01",
            "request_id": "gate:01",
            "idempotency_key": "c" * 64,
            "budget_lease": lease,
            "result": {"verdict": "CONTINUE", "reasons": ["gaps found"]},
            "error": None,
        }
        req_continue = report_min.request_from_chain(gate_continue, [net_env])
        rep_continue = report_min.run_report(req_continue)
        self.assertIsNone(rep_continue["result"]["report"])
        self.assertEqual(rep_continue["result"]["verdict"], "CONTINUE")
        self.assertEqual(rep_continue["result"]["counts"]["claims"], 0)

        # Case 2: STOP_SUCCESS verdict -> report is assembled
        gate_stop = copy.deepcopy(gate_continue)
        gate_stop["result"]["verdict"] = "STOP_SUCCESS"
        gate_stop["result"]["reasons"] = ["quality ok"]
        req_stop = report_min.request_from_chain(gate_stop, [net_env])
        rep_stop = report_min.run_report(req_stop)
        self.assertIsNotNone(rep_stop["result"]["report"])
        self.assertEqual(rep_stop["result"]["verdict"], "STOP_SUCCESS")
        self.assertEqual(rep_stop["result"]["counts"]["claims"], 1)

    def test_pipeline_citation_exclusion_filter_logic(self) -> None:
        """Simulates pipeline.py citation filtering to verify invalid/empty locators are dropped."""
        claims = [
            {
                "claim_id": "c1",
                "text": "Valid claim",
                "citations": [{"locator": "https://arxiv.org/abs/1"}],
            },
            {
                "claim_id": "c2",
                "text": "Invalid claim empty locator",
                "citations": [{"locator": ""}],
            },
            {
                "claim_id": "c3",
                "text": "Invalid claim None locator",
                "citations": [{"locator": None}],
            },
            {
                "claim_id": "c4",
                "text": "No citations",
                "citations": [],
            },
        ]

        valid_claims = []
        dropped_claims = []
        for c in claims:
            cits = [cit for cit in c.get("citations", []) if cit.get("locator")]
            if cits:
                c["citations"] = cits
                valid_claims.append(c)
            else:
                dropped_claims.append({
                    "claim_id": c.get("claim_id", "claim:unverified"),
                    "reason": "no_resolvable_citation",
                })

        self.assertEqual(len(valid_claims), 1)
        self.assertEqual(valid_claims[0]["claim_id"], "c1")
        self.assertEqual(len(dropped_claims), 3)
        dropped_ids = [d["claim_id"] for d in dropped_claims]
        self.assertListEqual(dropped_ids, ["c2", "c3", "c4"])

    def test_wiki_stage_package_intake_success(self) -> None:
        """Verifies wiki-stage packages the live output directory with full source traceability."""
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = pathlib.Path(tmp)
            pkg = wiki_stage.build_stage_package(LIVE_FULL_DIR, dest_dir)
            self.assertTrue(pkg.package_path.exists())
            self.assertTrue(pkg.package_id.startswith("rp_"))
            self.assertGreater(len(pkg.manifest.get("artifacts", [])), 0)
            self.assertGreater(len(pkg.manifest.get("sources", [])), 0)


if __name__ == "__main__":
    unittest.main()
