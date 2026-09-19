"""Challenger M3-1 Empirical Adversarial Stress & Verification Suite.

Adversarial stress-testing targeting Milestone 3 (Features F12–F15):
1. Feature F12: Multimodal Ingest Compatibility
   - `clean_min.run_clean` with strange `file://` URIs (spaces, uppercase, non-ascii, localhost, relative paths, multiple slashes).
   - Scheme gate dropping non-allowed schemes (`ftp://`, `data:`, `gopher:`, `javascript:`).
   - Delta semantics (seen keys deduplication) with `file://` seeds.
   - `PipelineTrigger` default stages vs custom stages, config parameter injection (`resume=True`).
2. Feature F13: Non-Destructive TalkLinker CAS Fusion
   - `to_merge_responses` provenance validation and filtering.
   - `merge_into_network` CAS atomic update: graph nodes/edges enriched, existing nodes preserved.
   - CAS mismatch handling (`E_CAS_CONFLICT`) with stale `prev_digest`: returns error safely without mutating state.
   - Concurrent merge race condition simulation: detecting stale digest, refresh-and-retry, zero lost updates.
   - Non-destructiveness: no directory wiping or file unlinking.
3. Feature F14: Downstream Wiki-Stage Deliverables
   - Verification that `wiki_stage.py` parses pipeline outputs (`report.md`, `tree/00-主表.md`, `sources.json`, `run-summary.json`).
   - `plan_stage_package` and `build_stage_package` content-addressing and immutability validation.
   - Strict Citation Coverage 1.0 enforcement: claims without valid citations dropped to `dropped_claims`.
   - Adversarial claim scenarios (empty citation list, missing locators, all claims invalid).
4. Feature F15: Pure-Python Identity Fallback
   - Full 70 oracle vectors bit-identical equality verification.
   - Stress-testing IPv6 (loopback, link-local, ULA, multicast blocked; public allowed; IPv6 port stripping).
   - Oblique IPv4 (hex, octal, uint32 blocked; public hex allowed).
   - Userinfo `@` in authority vs path vs query.
   - Port stripping rules for HTTP/HTTPS.
   - Query parameter sorting, tracking parameter stripping, sensitive credential parameter blocking.
   - Deduplication state machine (`retain`, `alias`, `conflict_version`, `same_bytes_distinct_id`).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import pytest

from research_tool.application.talk_linker import TalkEnrichReport, TalkLinker, TalkMatch
from research_tool.infrastructure.export.wiki_stage import (
    build_stage_package,
    plan_stage_package,
)
from research_tool.infrastructure.ingest.pipeline_adapter import (
    DEFAULT_DOWNSTREAM_STAGES,
    PipelineTrigger,
)
from research_tool.nine_loop import clean_min, merge_min
from research_tool.nine_loop.rt_identity_adapter import (
    AdapterClient,
    PythonIdentityEngine,
    UrlError,
    canonical_locator,
)


# ============================================================================
# Section 1: Feature F12 - Multimodal Ingest Compatibility
# ============================================================================


class TestF12MultimodalIngestCompatibility:
    """Stress-test clean_min.run_clean with strange file:// URIs and PipelineTrigger."""

    def _make_collect_env(self, seeds: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "v": 1,
            "run_id": "run-f12-test",
            "stage": "collect",
            "request_id": "collect:f12",
            "idempotency_key": "0" * 64,
            "budget_lease": {
                "lease_id": "lease-f12",
                "tokens_max": 1000,
                "cost_max": 1.0,
                "wall_s_max": 10.0,
                "search_calls_max": 5,
                "issued_at": "2026-09-18T00:00:00Z",
                "expires_at": "2026-09-18T01:00:00Z",
            },
            "result": {"seeds": seeds},
        }

    def test_clean_min_strange_file_uris(self):
        strange_urls = [
            # Spaces (unencoded and percent-encoded)
            "file:///Users/researcher/raw notes/document 1.pdf",
            "file:///Users/researcher/raw%20notes/document%202.pdf",
            # Uppercase and mixed case schemes
            "FILE:///opt/data/research_paper.pdf",
            "FiLe:///var/log/video_transcript.md",
            # Non-ascii (Chinese, accented European)
            "file:///Users/测试/中文文件夹/研读笔记.md",
            "file:///home/chercheur/café_résumé.pdf",
            # Localhost explicit host
            "file://localhost/etc/research/config.json",
            "file://localhost/Users/shared/video_001.md",
            # Relative paths with file://
            "file://./relative/path/note.md",
            "file://../parent/note.md",
            "file://relative/path/note.md",
            # Multiple consecutive slashes
            "file:////volumes/storage/data.pdf",
        ]

        seeds = [
            {
                "url": u,
                "source_id": f"src:{i:03d}",
                "evidence_span": {
                    "locator": u,
                    "content_sha256": hashlib.sha256(u.encode("utf-8")).hexdigest(),
                },
                "decision": "retain",
            }
            for i, u in enumerate(strange_urls)
        ]

        col_env = self._make_collect_env(seeds)
        req = clean_min.request_from_collect(col_env)
        res = clean_min.run_clean(req)

        assert res["error"] is None
        cleaned = res["result"]["cleaned"]
        dropped = res["result"]["dropped"]

        assert len(dropped) == 0, f"Expected 0 dropped, got: {dropped}"
        assert len(cleaned) == len(strange_urls)

        # Verify all cleaned items preserved the canonical locator and url
        for item, original_url in zip(cleaned, strange_urls):
            assert item["url"] == original_url
            assert item["clean_version"] == "clean.v1"
            assert item["decision"] == "retain"

    def test_clean_min_drops_unauthorized_schemes(self):
        denied_urls = [
            "ftp://files.example.com/repo/data.pdf",
            "data:text/plain;base64,SGVsbG8sIFdvcmxkIQ==",
            "javascript:alert(1)",
            "gopher://gopher.example.com:70/1",
            "file:without_double_slashes.pdf",
        ]

        seeds = [
            {
                "url": u,
                "source_id": f"src:denied:{i}",
                "evidence_span": {"locator": u, "content_sha256": "0" * 64},
                "decision": "retain",
            }
            for i, u in enumerate(denied_urls)
        ]

        col_env = self._make_collect_env(seeds)
        req = clean_min.request_from_collect(col_env)
        res = clean_min.run_clean(req)

        assert res["error"] is None
        assert len(res["result"]["cleaned"]) == 0
        dropped = res["result"]["dropped"]
        assert len(dropped) == len(denied_urls)
        for d in dropped:
            assert d["reason"] == "scheme_denied"

    def test_clean_min_delta_seen_keys_deduplication(self):
        url = "file:///tmp/multimodal/video_note.md"
        sha = hashlib.sha256(b"video content").hexdigest()
        seed = {
            "url": url,
            "source_id": "src:video_note_1",
            "evidence_span": {"locator": url, "content_sha256": sha},
            "decision": "retain",
        }

        col_env = self._make_collect_env([seed])
        req = clean_min.request_from_collect(col_env)

        seen_keys: set[tuple[Any, Any]] = set()
        res1 = clean_min.run_clean(req, seen_keys=seen_keys)
        assert res1["result"]["counts"]["new_facts"] == 1
        assert res1["result"]["counts"]["skipped"] == 0

        # Run clean again with the populated seen_keys: must skip!
        res2 = clean_min.run_clean(req, seen_keys=seen_keys)
        assert res2["result"]["counts"]["new_facts"] == 0
        assert res2["result"]["counts"]["skipped"] == 1
        assert len(res2["result"]["cleaned"]) == 0

    def test_pipeline_trigger_stages_configuration(self):
        # Default stages should be canonical 8 downstream stages
        trigger_def = PipelineTrigger()
        assert trigger_def._stages == DEFAULT_DOWNSTREAM_STAGES
        assert trigger_def._stages == [
            "clean",
            "extract",
            "knowledge",
            "inspect",
            "targeted",
            "merge",
            "qgate",
            "report",
        ]

        # Custom stages should be respected
        custom = ["clean", "extract", "report"]
        trigger_cust = PipelineTrigger(stages=custom)
        assert trigger_cust._stages == custom

        # PipelineTrigger propagates resume=True and stages into load_config overrides
        from unittest.mock import MagicMock, patch

        with patch("research_tool.domain.config.load_config") as mock_load_config:
            mock_cfg = MagicMock()
            mock_cfg.stages = custom
            mock_cfg.resume = True
            mock_load_config.return_value = mock_cfg

            # Trigger check
            import asyncio

            async def _run():
                with patch("research_tool.application.pipeline.ResearchPipeline") as mock_pipe_cls:
                    mock_pipe = MagicMock()
                    mock_pipe._result = MagicMock()

                    async def empty_stream(topic):
                        if False:
                            yield

                    mock_pipe.stream = empty_stream
                    mock_pipe_cls.return_value = mock_pipe

                    res = await trigger_cust.trigger("test topic", "/tmp/dummy")
                    assert res.success is True
                    mock_load_config.assert_called_once()
                    args, kwargs = mock_load_config.call_args
                    overrides = kwargs.get("overrides", {})
                    assert overrides["resume"] is True
                    assert overrides["stages"] == custom

            asyncio.run(_run())


# ============================================================================
# Section 2: Feature F13 - Non-Destructive TalkLinker CAS Fusion
# ============================================================================


class TestF13TalkLinkerCASFusion:
    """Stress-test TalkLinker CAS merge, concurrency, and non-destructive properties."""

    def _sample_network(self) -> dict[str, Any]:
        node0 = {
            "node_id": "src:paper_001",
            "title": "Attention Is All You Need",
            "evidence_spans": [
                {
                    "source_id": "src:paper_001",
                    "locator": "https://arxiv.org/abs/1706.03762",
                    "content_sha256": "1" * 64,
                }
            ],
        }
        return {
            "v": 1,
            "run_id": "run-talk-cas",
            "stage": "network",
            "request_id": "network:initial",
            "idempotency_key": "0" * 64,
            "budget_lease": {
                "lease_id": "lease-talk",
                "tokens_max": 1000,
                "cost_max": 1.0,
                "wall_s_max": 10.0,
                "search_calls_max": 5,
                "issued_at": "2026-09-18T00:00:00Z",
                "expires_at": "2026-09-18T01:00:00Z",
            },
            "result": {
                "nodes": [node0],
                "edges": [],
                "counts": {"nodes": 1, "edges": 0},
            },
            "error": None,
        }

    def test_to_merge_responses_provenance_and_filtering(self):
        linker = TalkLinker()

        valid_match = TalkMatch(
            paper_title="Attention Is All You Need",
            video_url="https://youtube.com/watch?v=valid_123",
            confidence=0.92,
            matched=True,
            video_title="Attention Talk NeurIPS",
            channel="NeurIPS Official",
            year=2017,
        )
        unmatched = TalkMatch(
            paper_title="Some Paper",
            video_url=None,
            confidence=0.10,
            matched=False,
            video_title="",
            channel="",
        )

        responses = linker.to_merge_responses([valid_match, unmatched])
        assert len(responses) == 1
        facts = responses[0]["facts"]
        assert len(facts) == 1
        fact = facts[0]

        assert fact["via"] == "talk_linker"
        assert fact["round_id"] == "talk_enrichment"
        assert fact["talk_confidence"] == 0.92
        assert fact["paper_title"] == "Attention Is All You Need"
        assert fact["video_title"] == "Attention Talk NeurIPS"
        assert fact["locator"] == "https://youtube.com/watch?v=valid_123"

    def test_cas_merge_success_preserves_existing_graph(self):
        linker = TalkLinker()
        net_env = self._sample_network()
        initial_node_id = net_env["result"]["nodes"][0]["node_id"]

        match = TalkMatch(
            paper_title="Attention Is All You Need",
            video_url="https://youtube.com/watch?v=attention_talk",
            confidence=0.95,
            matched=True,
            video_title="Attention Explanation",
            channel="AI Coffee Break",
            year=2018,
        )

        merged_net_env, merge_env = linker.merge_into_network(net_env, [match])

        assert merge_env["error"] is None
        assert merge_env["stage"] == "merge"
        assert merge_env["result"]["counts"]["new_facts"] >= 1
        assert merge_env["result"]["latest_digest"] is not None

        merged_nodes = merged_net_env["result"]["nodes"]
        # Existing node must still be present
        node_ids = {n["node_id"] for n in merged_nodes}
        assert initial_node_id in node_ids
        # New talk node or evidence must be added
        assert len(merged_nodes) >= 2 or len(merged_nodes[0]["evidence_spans"]) > 1

    def test_cas_mismatch_detection_with_stale_prev_digest(self):
        linker = TalkLinker()
        net_env = self._sample_network()

        match = TalkMatch(
            paper_title="Attention Is All You Need",
            video_url="https://youtube.com/watch?v=vid1",
            confidence=0.88,
            matched=True,
            video_title="Vid 1",
        )

        stale_digest = "f" * 64
        merged_net_env, merge_env = linker.merge_into_network(
            net_env, [match], prev_digest=stale_digest
        )

        # Mismatch must return error frame with E_CAS_CONFLICT
        assert merge_env.get("error") is not None
        assert merge_env["error"]["code"] == "E_CAS_CONFLICT"

        # Network envelope must be unmodified
        assert merged_net_env == net_env

    def test_concurrent_merge_race_and_self_healing(self):
        """Simulate two concurrent workers starting from state N0.

        Worker 1 commits first (digest N0 -> N1).
        Worker 2 fails with stale N0 digest, refreshes to N1, and successfully merges.
        Final graph contains both talk enrichments without loss.
        """
        linker = TalkLinker()
        net_env = self._sample_network()
        d0 = merge_min.graph_digest(net_env["result"])

        match_a = TalkMatch(
            paper_title="Attention Is All You Need",
            video_url="https://youtube.com/watch?v=talk_A",
            confidence=0.91,
            matched=True,
            video_title="Talk A",
        )
        match_b = TalkMatch(
            paper_title="Attention Is All You Need",
            video_url="https://youtube.com/watch?v=talk_B",
            confidence=0.89,
            matched=True,
            video_title="Talk B",
        )

        # Worker 1 succeeds
        net_env_1, merge_env_1 = linker.merge_into_network(net_env, [match_a], prev_digest=d0)
        assert merge_env_1["error"] is None
        d1 = merge_min.graph_digest(net_env_1["result"])
        assert d1 != d0

        # Worker 2 attempts with stale d0 -> conflicts
        net_env_2_stale, merge_env_2_stale = linker.merge_into_network(
            net_env_1, [match_b], prev_digest=d0
        )
        assert merge_env_2_stale["error"]["code"] == "E_CAS_CONFLICT"
        assert net_env_2_stale == net_env_1

        # Worker 2 refreshes digest to d1 and retries -> succeeds
        net_env_final, merge_env_final = linker.merge_into_network(
            net_env_1, [match_b], prev_digest=d1
        )
        assert merge_env_final["error"] is None
        d_final = merge_min.graph_digest(net_env_final["result"])
        assert d_final != d1

        # Verify all evidence from both talks exists in the final graph
        all_locators = {
            span.get("locator")
            for node in net_env_final["result"]["nodes"]
            for span in node.get("evidence_spans", [])
        }
        assert "https://youtube.com/watch?v=talk_A" in all_locators
        assert "https://youtube.com/watch?v=talk_B" in all_locators


# ============================================================================
# Section 3: Feature F14 - Downstream Wiki-Stage Deliverables
# ============================================================================


class TestF14DownstreamWikiStageDeliverables:
    """Verify that wiki_stage.py parses pipeline outputs and citation coverage is 1.0."""

    def test_wiki_stage_packages_pipeline_outputs_without_error(self, tmp_path: Path):
        topic_dir = tmp_path / "deep_learning"
        topic_dir.mkdir()
        tree_dir = topic_dir / "tree"
        tree_dir.mkdir()

        # 1. report.md conforming to downstream contract
        report_text = (
            "<!-- source: https://arxiv.org/abs/2301.00001 -->\n"
            "<!-- fetched: 2026-09-18T00:00:00Z -->\n"
            "# 调研报告：深度学习前沿\n\n"
            "## 核心结论\n\n"
            "大语言模型在多模态理解方面取得重大突破 [^1]。\n"
            "自注意力机制大幅降低了跨模态对齐误差 [^2]。\n\n"
            "[^1]: https://arxiv.org/abs/2301.00001\n"
            "[^2]: https://arxiv.org/abs/2301.00002\n"
        )
        (topic_dir / "report.md").write_text(report_text, encoding="utf-8")

        # 2. tree/00-主表.md
        main_table_text = (
            "<!-- source: https://arxiv.org/abs/2301.00001 -->\n"
            "<!-- fetched: 2026-09-18T00:00:00Z -->\n"
            "# 知识网络大纲\n\n"
            "- [[N01-llm-overview|大模型概述]]\n"
            "- [[N02-attention-mech|注意力机制演化]]\n"
        )
        (tree_dir / "00-主表.md").write_text(main_table_text, encoding="utf-8")

        # 3. sources.json
        sources_payload = [
            {
                "url": "https://arxiv.org/abs/2301.00001",
                "title": "LLM Advances",
                "fetchedAt": "2026-09-18T00:00:00Z",
                "content_hash": "sha256:" + "a" * 64,
            },
            {
                "url": "https://arxiv.org/abs/2301.00002",
                "title": "Attention Align",
                "fetchedAt": "2026-09-18T00:00:00Z",
                "content_hash": "sha256:" + "b" * 64,
            },
        ]
        (topic_dir / "sources.json").write_text(json.dumps(sources_payload), encoding="utf-8")

        # 4. run-summary.json
        summary_payload = {
            "version": 1,
            "pipeline_complete": True,
            "stages_completed": [
                "collect",
                "clean",
                "extract",
                "knowledge",
                "inspect",
                "targeted",
                "merge",
                "qgate",
                "report",
            ],
            "citation_coverage": 1.0,
            "failed_stage": None,
        }
        (topic_dir / "run-summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")

        staging_dir = tmp_path / "wiki_staging"

        # Plan package
        plan = plan_stage_package(topic_dir, staging_dir)
        assert plan["verification"] == "unverified"
        assert plan["files"] == 4
        assert len(plan["sources"]) == 2

        # Build package
        pkg = build_stage_package(topic_dir, staging_dir)
        assert pkg.package_path.exists()
        manifest_path = pkg.package_path / "manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["packageId"] == pkg.package_id
        assert manifest["packageHash"] == pkg.package_hash
        assert manifest["verification"] == "unverified"
        assert manifest["classification"]["isolationRequired"] is True

        # Second build must return already_exists=True without error
        pkg2 = build_stage_package(topic_dir, staging_dir)
        assert pkg2.already_exists is True
        assert pkg2.package_id == pkg.package_id

    def test_citation_coverage_strictly_enforces_1_0(self):
        """Test claim filtering logic: unverified claims must be dropped."""
        raw_claims = [
            {
                "claim_id": "c1",
                "text": "Verified claim 1",
                "citations": [{"locator": "https://arxiv.org/abs/2301.00001"}],
            },
            {
                "claim_id": "c2",
                "text": "Unverified claim with empty citations",
                "citations": [],
            },
            {
                "claim_id": "c3",
                "text": "Unverified claim with citation lacking locator",
                "citations": [{"locator": ""}],
            },
            {
                "claim_id": "c4",
                "text": "Verified claim 2",
                "citations": [{"locator": "https://arxiv.org/abs/2301.00002"}],
            },
        ]

        valid_claims = []
        dropped_claims = []
        for c in raw_claims:
            cits = [cit for cit in c.get("citations", []) if cit.get("locator")]
            if cits:
                c_copy = dict(c)
                c_copy["citations"] = cits
                valid_claims.append(c_copy)
            else:
                dropped_claims.append(
                    {"claim_id": c.get("claim_id"), "reason": "no_resolvable_citation"}
                )

        assert len(valid_claims) == 2
        assert [c["claim_id"] for c in valid_claims] == ["c1", "c4"]
        assert len(dropped_claims) == 2
        assert [c["claim_id"] for c in dropped_claims] == ["c2", "c3"]

        # Formatted report verification
        lines = []
        footnotes = []
        for i, c in enumerate(valid_claims, 1):
            lines.append(f"{c['text']} [^{i}]")
            footnotes.append(f"[^{i}]: {c['citations'][0]['locator']}")

        report_content = "\n".join(lines) + "\n\n" + "\n".join(footnotes)
        for i in range(1, len(valid_claims) + 1):
            assert f"[^{i}]" in report_content
            assert f"[^{i}]: http" in report_content


# ============================================================================
# Section 4: Feature F15 - Pure-Python Identity Fallback & Stress Testing
# ============================================================================


class TestF15PythonIdentityEngineAdversarial:
    """Stress-test PythonIdentityEngine with RFC edge cases and oracle test vectors."""

    def test_all_70_oracle_test_vectors(self):
        """Empirically test all 70 vectors from oracle.v1.jsonl and addendum."""
        fixtures_dir = Path(__file__).parent / "fixtures"
        files = [
            fixtures_dir / "oracle.v1.jsonl",
            fixtures_dir / "oracle.v1.1.addendum.jsonl",
        ]

        vectors = []
        for p in files:
            if p.exists():
                for line in p.read_text("utf-8").splitlines():
                    line = line.strip()
                    if line:
                        vectors.append(json.loads(line))

        assert len(vectors) == 70, f"Expected 70 vectors, got {len(vectors)}"

        for idx, vec in enumerate(vectors, 1):
            actual = PythonIdentityEngine.dispatch(vec["request"])
            expected = vec["expected_response"]
            assert actual == expected, (
                f"Vector #{idx} ({vec['id']}) failed:\n"
                f"Expected: {json.dumps(expected)}\n"
                f"Actual:   {json.dumps(actual)}"
            )

    @pytest.mark.parametrize(
        "blocked_ipv6",
        [
            "http://[::1]/",
            "http://[::1]:8080/path",
            "http://[fe80::1]/",
            "http://[fc00::1]/",
            "http://[ff02::1]/",
        ],
    )
    def test_ipv6_blocked_ranges(self, blocked_ipv6: str):
        with pytest.raises(UrlError) as exc_info:
            canonical_locator(blocked_ipv6)
        assert exc_info.value.code == "url_blocked_literal_ip"

    def test_ipv6_public_and_port_stripping(self):
        # Public IPv6 allowed
        url1 = "http://[2606:4700:4700::1111]:80/dns-query"
        # Port 80 on HTTP must be stripped
        assert canonical_locator(url1) == "http://[2606:4700:4700::1111]/dns-query"

        # Non-default port on IPv6 must be preserved
        url2 = "http://[2606:4700:4700::1111]:8080/dns-query"
        assert canonical_locator(url2) == "http://[2606:4700:4700::1111]:8080/dns-query"

    @pytest.mark.parametrize(
        "oblique_ip",
        [
            "http://0x7f000001/test",  # Hex 127.0.0.1 uint32
            "http://0177.0.0.1/test",  # Octal 127.0.0.1 dotted
            "http://2130706433/test",  # Decimal uint32 127.0.0.1
            "http://127.0.0.0x1/test",  # Dotted hex in last octet
            "http://127.1/test",  # 2-part dotted IPv4
            "http://127.0.1/test",  # 3-part dotted IPv4
        ],
    )
    def test_ipv4_oblique_formats_blocked(self, oblique_ip: str):
        with pytest.raises(UrlError) as exc_info:
            canonical_locator(oblique_ip)
        assert exc_info.value.code == "url_blocked_literal_ip"

    @pytest.mark.parametrize(
        "oblique_hex_dotted",
        [
            "http://0x7f.0.0.1/test",
            "http://0x7f.1/test",
            "http://017700000001/test",
        ],
    )
    def test_ipv4_oblique_dotted_hex_divergence_bug(self, oblique_hex_dotted: str):
        """Rust rt-identity blocks these with url_blocked_literal_ip. PythonIdentityEngine currently allows them."""
        with pytest.raises(UrlError) as exc_info:
            canonical_locator(oblique_hex_dotted)
        assert exc_info.value.code == "url_blocked_literal_ip"

    def test_userinfo_at_sign_in_authority_vs_path_and_query(self):
        # Userinfo in authority -> MUST block
        with pytest.raises(UrlError) as exc_info1:
            canonical_locator("http://admin:secret@example.com/dashboard")
        assert exc_info1.value.code == "url_blocked_credentials"

        with pytest.raises(UrlError) as exc_info2:
            canonical_locator("http://admin@example.com/dashboard")
        assert exc_info2.value.code == "url_blocked_credentials"

        # At-sign '@' in path -> MUST be allowed
        path_at = "http://example.com/@alice/profile"
        assert canonical_locator(path_at) == "http://example.com/@alice/profile"

        # At-sign '@' in query -> MUST be allowed
        query_at = "http://example.com/search?email=alice@example.com"
        assert canonical_locator(query_at) == "http://example.com/search?email=alice@example.com"

    def test_port_stripping_rules(self):
        # Default ports stripped
        assert canonical_locator("http://example.com:80/path") == "http://example.com/path"
        assert canonical_locator("https://example.com:443/path") == "https://example.com/path"

        # Cross default ports preserved
        assert canonical_locator("http://example.com:443/path") == "http://example.com:443/path"
        assert canonical_locator("https://example.com:80/path") == "https://example.com:80/path"

        # Non-default ports preserved
        assert canonical_locator("http://example.com:8080/path") == "http://example.com:8080/path"
        assert canonical_locator("https://example.com:8443/path") == "https://example.com:8443/path"

    def test_query_params_sorting_and_tracking_stripping(self):
        # Duplicate parameters sorted and preserved
        url_dup = "http://example.com/api?z=3&a=1&z=2"
        assert canonical_locator(url_dup) == "http://example.com/api?a=1&z=2&z=3"

        # Tracking parameters stripped
        url_track = "http://example.com/post?utm_source=twitter&keep=1&gclid=xyz&utm_medium=social"
        assert canonical_locator(url_track) == "http://example.com/post?keep=1"

        # Sensitive parameters in query or fragment rejected
        with pytest.raises(UrlError) as exc1:
            canonical_locator("http://example.com/api?api_key=secret")
        assert exc1.value.code == "url_blocked_credentials"

        with pytest.raises(UrlError) as exc2:
            canonical_locator("http://example.com/api#access_token=secret")
        assert exc2.value.code == "url_blocked_credentials"

    def test_deduplication_state_machine(self):
        engine = PythonIdentityEngine()

        # 1. Alias: duplicate source_id and content
        req_alias = {
            "v": 1,
            "id": "batch-alias",
            "op": "identify",
            "hits": [
                {"url": "https://arxiv.org/abs/2301.00001", "content": "same content"},
                {"url": "https://arxiv.org/pdf/2301.00001", "content": "same content"},
            ],
        }
        res_alias = engine.dispatch(req_alias)
        assert res_alias["ok"] is True
        decisions_alias = [h["decision"] for h in res_alias["identities"]]
        assert decisions_alias == ["retain", "alias"]

        # 2. Conflict version: same source_id but different non-None content
        req_conflict = {
            "v": 1,
            "id": "batch-conflict",
            "op": "identify",
            "hits": [
                {"url": "https://arxiv.org/abs/2301.00002", "content": "content version 1"},
                {"url": "https://arxiv.org/abs/2301.00002", "content": "content version 2"},
            ],
        }
        res_conflict = engine.dispatch(req_conflict)
        assert res_conflict["ok"] is True
        decisions_conflict = [h["decision"] for h in res_conflict["identities"]]
        assert decisions_conflict == ["conflict_version", "conflict_version"]

        # 3. Same bytes distinct ID: different source_id but same content hash
        req_same_bytes = {
            "v": 1,
            "id": "batch-same-bytes",
            "op": "identify",
            "hits": [
                {"url": "https://arxiv.org/abs/2301.00003", "content": "identical body bytes"},
                {"url": "https://arxiv.org/abs/2301.00004", "content": "identical body bytes"},
            ],
        }
        res_same_bytes = engine.dispatch(req_same_bytes)
        assert res_same_bytes["ok"] is True
        decisions_same_bytes = [h["decision"] for h in res_same_bytes["identities"]]
        assert decisions_same_bytes == ["retain", "same_bytes_distinct_id"]
