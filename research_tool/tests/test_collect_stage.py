"""Three-layer differential tests for the ① collect stage (P3 slice 1).

L1 — byte-identical identity stream vs the adapter golden path (real pinned
     child, 82-ID frozen fixture subset).
L2 — exact-id / split / alias determinations identical to V3-era core behavior
     (post-parse typed errors carry id; pre-parse records id=null).
L3 — full envelope per-field contract compliance, including failure-case
     error frames and the partial-output prohibition.

Filesystem-pure: no scratch, no /tmp, no repo-tree writes. The real child is
spawned with an empty environment exactly as the adapter does.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from research_tool.nine_loop import collect_stage as C
from research_tool.nine_loop import rt_identity_adapter as A

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

GOLDEN_IDS = [
    "fx-alias-two-engines", "fx-identical-twice",
    "fx-arxiv-abs-vs-pdf-alias", "fx-canonical-same-http",
    "fx-github-fork", "fx-doi-prefix-distinct",
    "fx-same-bytes-two-doi", "fx-null-vs-hash-distinct",
    "fx-github-git", "fx-github-nogit",
]


def sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def real_client(deadline_s: float = 30.0) -> A.AdapterClient:
    return A.AdapterClient(fallback=True, deadline_s=deadline_s)


def make_lease(wall_s_max=30.0):
    return {"lease_id": "lease-1", "tokens_max": 100000, "cost_max": 1.0,
            "wall_s_max": wall_s_max, "search_calls_max": 0,
            "issued_at": "2026-08-25T00:00:00Z",
            "expires_at": "2026-08-25T23:59:59Z"}


def make_request(seeds, req_id="req-001", crawl_policy=None, lease=None):
    req = {"v": 1, "run_id": "run-1", "stage": "collect", "request_id": req_id,
           "idempotency_key": C.compute_idempotency_key(
               {"seeds": seeds, "crawl_policy": crawl_policy or {}}),
           "budget_lease": lease or make_lease(),
           "seeds": seeds, "crawl_policy": crawl_policy or {}}
    return req


def seeds_from_fixture(case):
    return [{"url": h["url"], "content": h.get("content")}
            for h in case["request"]["hits"]]


def load_fixture(fid):
    for name in ("oracle.v1.jsonl", "oracle.v1.1.addendum.jsonl"):
        p = FIXTURES / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            if c["id"] == fid:
                return c
    raise KeyError(fid)


def direct_child_stdout(payload: bytes) -> bytes:
    client = real_client()
    return client.run_raw(payload).stdout_bytes


# --------------------------------------------------------------------------- #
# L1 — differential golden (byte-identical identity stream)
# --------------------------------------------------------------------------- #
class TestL1DifferentialGolden(unittest.TestCase):
    def test_identity_stream_byte_identical_to_adapter_golden(self):
        requests = []
        expected_streams = []
        for fid in GOLDEN_IDS:
            case = load_fixture(fid)
            seeds = seeds_from_fixture(case)
            req = make_request(seeds, req_id=f"gold-{fid}")
            payload = A.AdapterClient.encode_requests([C._identity_request(req)])
            expected_streams.append((fid, direct_child_stdout(payload)))
            requests.append((fid, req))
        client = real_client()
        for (fid, req), (fid2, expected) in zip(requests, expected_streams):
            self.assertEqual(fid, fid2)
            outcome = C.run_collect_outcome(req, client)
            self.assertIsNone(outcome.envelope["error"], fid)
            # L1 core assertion: byte-identical identity stream
            self.assertEqual(outcome.identity_stream, expected,
                             f"identity stream drift on {fid}")
            self.assertEqual(
                outcome.envelope["result"]["identity_stream_sha256"],
                hashlib.sha256(expected).hexdigest())

    def test_raw_snapshot_digest_is_content_addressed(self):
        case = load_fixture("fx-alias-two-engines")
        req = make_request(seeds_from_fixture(case), req_id="dig-1")
        outcome = C.run_collect_outcome(req, real_client())
        digest = outcome.envelope["result"]["raw_snapshot_digest"]
        self.assertEqual(digest, hashlib.sha256(outcome.identity_stream).hexdigest())
        self.assertEqual(len(digest), 64)


# --------------------------------------------------------------------------- #
# L2 — exact-id / split / alias parity with V3-era core behavior
# --------------------------------------------------------------------------- #
class TestL2SemanticsParity(unittest.TestCase):
    def test_null_vs_hash_shared_family_two_rows(self):
        case = load_fixture("fx-null-vs-hash-distinct")
        outcome = C.run_collect_outcome(
            make_request(seeds_from_fixture(case), req_id="nvh"), real_client())
        seeds = outcome.envelope["result"]["seeds"]
        self.assertEqual(len(seeds), 2)
        families = {s["source_id"] for s in seeds}
        self.assertEqual(len(families), 1)  # same arxiv family
        self.assertTrue(all(s["decision"] == "retain" for s in seeds))

    def test_same_bytes_two_doi_distinct_families(self):
        case = load_fixture("fx-same-bytes-two-doi")
        outcome = C.run_collect_outcome(
            make_request(seeds_from_fixture(case), req_id="sbt"), real_client())
        seeds = outcome.envelope["result"]["seeds"]
        self.assertEqual(len(seeds), 2)
        self.assertEqual(len({s["source_id"] for s in seeds}), 2)

    def test_alias_fixture_keeps_rows_in_one_family(self):
        """V3-era parity: the core never drops rows; a repeated full composite
        is labeled alias while both rows stay in the same source family."""
        case = load_fixture("fx-alias-two-engines")
        outcome = C.run_collect_outcome(
            make_request(seeds_from_fixture(case), req_id="al"), real_client())
        seeds = outcome.envelope["result"]["seeds"]
        self.assertEqual(len(seeds), 2)
        self.assertEqual(len({s["source_id"] for s in seeds}), 1)
        decisions = {s["decision"] for s in seeds}
        self.assertIn("alias", decisions)
        # verbatim stream: both raw urls retained across the two rows' aliases
        records = [json.loads(line)
                   for line in outcome.identity_stream.decode().splitlines()]
        self.assertEqual(len(records), 1)
        alias_locators = {a["locator"]
                          for ident in records[0]["identities"]
                          for a in ident["aliases"]}
        self.assertEqual(alias_locators,
                         {h["url"] for h in case["request"]["hits"]})

    def test_post_parse_error_carries_id(self):
        # V3-era parity: post-parse typed errors carry the request id
        client = real_client()
        depth_req = {"v": 1, "id": "d1", "op": "identify", "hits": [],
                     "nested": [[[[[[[[[0]]]]]]]]]}
        rec = client.run_raw(A.AdapterClient.encode_requests([depth_req]))
        self.assertEqual(rec.records[0]["id"], "d1")
        self.assertEqual(rec.records[0]["error"]["code"], "limit_depth")

    def test_pre_parse_error_id_is_null(self):
        client = real_client()
        rec = client.run_raw(b"x" * 1048577 + b"\n")
        self.assertIsNone(rec.records[0]["id"])
        self.assertEqual(rec.records[0]["error"]["code"], "limit_line")


# --------------------------------------------------------------------------- #
# L3 — envelope per-field compliance + failure frames
# --------------------------------------------------------------------------- #
class TestL3Envelope(unittest.TestCase):
    def test_success_envelope_full_compliance(self):
        case = load_fixture("fx-alias-two-engines")
        req = make_request(seeds_from_fixture(case), req_id="env-1")
        env = C.run_collect(req, real_client())
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["run_id"], "run-1")
        self.assertEqual(env["stage"], "collect")
        self.assertEqual(env["request_id"], "env-1")
        self.assertEqual(env["idempotency_key"], req["idempotency_key"])
        self.assertEqual(env["budget_lease"], req["budget_lease"])
        self.assertIsNone(env["error"])
        result = env["result"]
        self.assertEqual(len(result["raw_snapshot_digest"]), 64)
        self.assertEqual(result["failures"], [])
        for seed in result["seeds"]:
            span = seed["evidence_span"]
            for field in ("source_id", "content_sha256", "locator",
                          "extractor_version", "round_id"):
                self.assertIn(field, span)
            self.assertEqual(span["extractor_version"], C.EXTRACTOR_VERSION)
            self.assertEqual(span["round_id"], "env-1")

    def test_timeout_error_frame_no_partial_output(self):
        exe = sys.executable
        client = A.AdapterClient(exe, sha256_file(exe), deadline_s=0.5,
                                 command=[exe, "-c",
                                          "import time; time.sleep(30)"],
                                 fallback=False)
        req = make_request(
            [{"url": "http://example.com/SECRETMARKER"}], req_id="t1",
            lease=make_lease(wall_s_max=0.5))
        env = C.run_collect(req, client)
        self.assertIsNone(env["result"])  # partial-output forbidden
        frame = env["error"]
        self.assertEqual(frame["code"], C.E_BUDGET_EXHAUSTED)
        self.assertEqual(frame["stage"], "collect")
        self.assertFalse(frame["retryable"])
        self.assertNotIn("SECRETMARKER", json.dumps(env))

    def test_child_internal_error_frame(self):
        exe = sys.executable
        client = A.AdapterClient(exe, sha256_file(exe),
                                 command=[exe, "-c", "import sys; sys.exit(3)"],
                                 fallback=False)
        env = C.run_collect(make_request([{"url": "http://example.com/"}],
                                         req_id="c3"), client)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], C.E_FATAL)
        self.assertFalse(env["error"]["retryable"])

    def test_contract_violation_error_frame(self):
        exe = sys.executable
        client = A.AdapterClient(exe, sha256_file(exe),
                                 command=[exe, "-c",
                                          "import sys; sys.stdout.write('noise\\n')"],
                                 fallback=False)
        env = C.run_collect(make_request([{"url": "http://example.com/"}],
                                         req_id="cv"), client)
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], C.E_CONTRACT)

    def test_binary_mismatch_refuses_and_maps_to_frame(self):
        with self.assertRaises(A.AdapterBinaryMismatch):
            A.AdapterClient(sys.executable, "0" * 64, fallback=False)

        class MismatchClient:
            deadline_s = 5.0

            def encode_requests(self, requests):
                return A.AdapterClient.encode_requests(requests)

            def run_raw(self, payload):
                raise A.AdapterBinaryMismatch("pinned sha mismatch")

        env = C.run_collect(make_request([{"url": "http://example.com/"}],
                                         req_id="bm"), MismatchClient())
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], C.E_FATAL)
        self.assertIn("sha256 mismatch", env["error"]["safe_message"])


# --------------------------------------------------------------------------- #
# Envelope validation frames (contract §2/§4)
# --------------------------------------------------------------------------- #
class TestEnvelopeValidation(unittest.TestCase):
    def base(self):
        return make_request([{"url": "http://example.com/"}])

    def assert_frame(self, request, code):
        env = C.run_collect(request, real_client())
        self.assertIsNone(env["result"])
        self.assertEqual(env["error"]["code"], code)

    def test_version_mismatch(self):
        req = self.base()
        req["v"] = 2
        self.assert_frame(req, C.E_VERSION)

    def test_stage_mismatch(self):
        req = self.base()
        req["stage"] = "clean"
        self.assert_frame(req, C.E_SCHEMA)

    def test_missing_lease(self):
        req = self.base()
        del req["budget_lease"]
        self.assert_frame(req, C.E_LEASE_INVALID)

    def test_lease_missing_field(self):
        req = self.base()
        del req["budget_lease"]["wall_s_max"]
        self.assert_frame(req, C.E_LEASE_INVALID)

    def test_idempotency_conflict(self):
        req = self.base()
        req["idempotency_key"] = "f" * 64
        self.assert_frame(req, C.E_IDEMPOTENCY_CONFLICT)

    def test_empty_seeds(self):
        req = make_request([], req_id="empty")
        self.assert_frame(req, C.E_SCHEMA)

    def test_seed_url_missing(self):
        req = make_request([{"title": "no url"}], req_id="nourl")
        self.assert_frame(req, C.E_SCHEMA)


# --------------------------------------------------------------------------- #
# Static hygiene
# --------------------------------------------------------------------------- #
class TestStaticHygiene(unittest.TestCase):
    def test_no_network_imports_in_stage(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "nine_loop/collect_stage.py").read_text(
            encoding="utf-8")
        for banned in ("socket", "urllib", "http.client", "requests",
                       "ssl", "asyncio"):
            self.assertNotIn(f"import {banned}", src)
            self.assertNotIn(f"from {banned}", src)

    def test_stage_constant(self):
        self.assertEqual(C.STAGE, "collect")
        self.assertEqual(C.CONTRACT_VERSION, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
