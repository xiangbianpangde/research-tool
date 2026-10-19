# Portable LLM and Bounded Research Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one canonical macOS CLI/SDK project, permit endpoint/model replacement without credential crossover, and bound real-network research while retaining exact evidence validation.

**Architecture:** Retain ResearchPipeline as the only nine-stage dispatcher. Configure external LLM identity independently of research behavior; put deadline/cancellation controls at network and subprocess boundaries. Validate evidence before deduplication and each extracted chunk before publishing aggregate output. No quality-gate bypass or replacement research pipeline.

**Tech Stack:** Python 3.11+, asyncio, httpx, Pydantic v2, OpenAI/Anthropic optional SDKs, pytest/pytest-asyncio, Ruff, POSIX/macOS.

## Global Constraints

- Canonical source is `/Users/xbpd/Projects/research-tool`, based on `96c18be`, branch `fix/runtime-input-reliability`.
- Old repositories and dirty changes remain in `/Users/xbpd/Projects/archives/research_tool_archive/versions/2026-10-17T1840+0800/`; never copy their entire implementations into canonical.
- Formal chain remains collect, clean, extract, knowledge, inspect, targeted, merge, qgate, report. CONTINUE triggers replenishment; only STOP_SUCCESS publishes final.
- No WebUI/Gradio, new project copies, production credential persistence, secret logging, automatic commit/push/merge, or permanent deletion.
- Exact source quotes, offsets and hashes remain mandatory. Do not lower gate thresholds to manufacture success.
- Test credentials are process-local. The selected test model is not a product default; context size remains unknown until supported by endpoint metadata or authoritative evidence.
- Parent owns docs, integration, evidence-filter ordering and live acceptance. Workers own disjoint source/test files listed below.
- Validation artifacts belong in ignored `research-output/portability-reliability-20261017/`, not Desktop/home/temp directories.

---

### Task 1: Configurable LLM boundary

**Files:** `research_tool/domain/models.py` (LLMConfig/Provider only), `research_tool/domain/config.py`, `research_tool/infrastructure/llm/base.py`, `research_tool/infrastructure/llm/openai_client.py`, new `research_tool/tests/test_llm_portability.py`. Existing provider tests may be adjusted only to isolate added environment variables.

**Interfaces:** Keep `load_config(path=None, *, overrides=None) -> PipelineConfig` and `LLMClient.from_config(config)`. Add provider `openai_compatible`, requiring explicit base_url/model; use `LLM_API_KEY` as its process-level secret. Preserve current explicit override > environment > YAML precedence and provider-specific credentials. Add only narrowly scoped request options needed for tested portability.

- [ ] Add regression cases for openai_compatible dispatch and validation, generic key injection, CLI/config endpoint switch without old credentials, configured request options, and response_format rejection with no fallback on auth/quota/timeouts.
- [ ] Run `python -B -m pytest -q -p no:cacheprovider research_tool/tests/test_llm_portability.py` and record expected failures before production edits.
- [ ] Implement minimum model/config/factory/client changes; no literal test endpoint, credential or model defaults in product code. A JSON compatibility retry occurs only on a clearly unsupported response_format error; retain schema validation and bounded call wall clock.
- [ ] Run new tests plus test_config.py, test_config_sdk_coverage.py, test_llm_core_coverage.py, test_llm_client_auth.py and test_llm_fail_fast.py. Run Ruff on owned files.
- [ ] Return changed paths and RED/GREEN commands. Do not commit.

### Task 2: Fetch/DNS/OCR bounded execution

**Files:** `research_tool/common/url_guard.py`, `research_tool/infrastructure/stages/fetcher.py`, `research_tool/infrastructure/ingest/ocr.py`, `research_tool/infrastructure/ingest/pdf.py`, new `research_tool/tests/test_fetch_dns_ocr_deadlines.py`. Existing fetch/OCR tests may be adapted only for new boundary signatures.

**Interfaces:** Preserve synchronous `assert_safe_url(url)` for sync callers. Add an asynchronous guarded boundary for async fetch and redirect hooks, with strict DNS failure/private-address rejection and bounded wall time. Preserve `Fetcher.fetch(url) -> FetchResult`; failures do not become content. OCR gets finite timeout and cancellation-aware execution; terminate/reap child processes rather than leave background OCR alive.

- [ ] Add non-network tests that expose event-loop blocking DNS, guard excluded from page timeout, OCR timeout and cancellation leaving a process alive.
- [ ] Run targeted tests before edits and retain RED evidence.
- [ ] Implement DNS off event loop with bounded resource use; keep SSRF checks for every redirect. Move guard inside Fetcher wall clock. Add subprocess deadline and cancellation propagation to OCR path without modifying domain/models.py.
- [ ] Run new tests plus test_fetcher_hard_timeout.py and existing fetcher/PDF/OCR/SSRF tests discovered by glob. Run Ruff on owned files.
- [ ] Return behavior limits (including DNS thread shutdown caveats) and exact test results. Do not commit.

### Task 3: Search and incremental collection

**Files:** `research_tool/infrastructure/stages/collector.py`, `research_tool/infrastructure/search/_http.py`, new `research_tool/tests/test_collector_bounded_runtime.py`. Do not edit fetcher, domain models or pipeline.

**Interfaces:** Keep Collector.search_queries and fetch_and_store signatures backward compatible. A backend query is bounded by existing collector.timeout_sec; queued combinations must have an explicit finite request ceiling, not only a retained-results limit. Sources/audits and raw body files survive partial completion/cancellation, while pipeline stage success is still controlled by dispatcher.

- [ ] Add tests with one never-ending backend, expanded engine/query/page/sort combinations and cancellation after one completed fetch; use Events or small controlled deadlines, not real internet.
- [ ] Run new tests before production edits and retain RED evidence.
- [ ] Implement deadline propagation through search retries, finite combination budget and cancellation cleanup. Persist successful fetch outputs incrementally with coherent source/audit metadata; keep deterministic identifiers, deduplication, official-source requirements and existing funnel counters.
- [ ] Run new tests plus existing collector/source-audit/search HTTP tests; run Ruff on owned files.
- [ ] Return chosen request ceiling and warning behavior. Do not commit.

### Task 4: Evidence-first cleaning and chunk extraction

**Files:** Parent owns `research_tool/application/pipeline.py`, `research_tool/infrastructure/stages/cleaner.py`, `research_tool/infrastructure/stages/extractor.py`, new `research_tool/tests/test_evidence_runtime_reliability.py`.

**Interfaces:** `Cleaner.process` retains defaults for old standalone callers, with opt-in body-only evidence filtering for formal pipeline before deduplication. Extractor prompt body must equal `parse_source(...)["text"]`. Each chunk validates quotes against both provided chunk and canonical source before accepting results. Verified cache identity includes source body, config, model/endpoint identity and contract version; no credential values stored.

- [ ] Add tests for longer discovery beating body in deduplication, YAML metadata excluded from prompt, invalid quote failing before queued jobs start, and successful chunks reused after a transient sibling failure.
- [ ] Observe RED with `python -B -m pytest -q -p no:cacheprovider research_tool/tests/test_evidence_runtime_reliability.py`.
- [ ] Implement pre-dedup evidence filtering, shared body parser and per-chunk early verification/cache. Invalid/unbound evidence fails closed; transient error recovery never republishes partial final output.
- [ ] Run targeted cleaner/extractor/pipeline tests and new tests. Inspect cache invalidation on source/model/config change.

### Task 5: Archive policy and current-path documentation

**Files:** Parent owns root Agent.md, README.md, STATUS.md, `docs/plan/PROJECT-VERSION-POLICY.md`, archive root README.md and version RESTORE.md/canonical-selection.json.

- [ ] Write one-entry policy: daily development only canonical, versions in Git branches/tags, snapshots under non-prefix archive root, manifest+dirty preservation+hashes required before future migrations, ignored research-output for run artifacts.
- [ ] Update current (not immutable historical) development paths and add historical original-path → archived-path mapping.
- [ ] Validate all archived HEADs/statuses, Git worktree pointers and bare fsck. Record absence of pre-move byte hashes honestly.
- [ ] Remove only current empty `.session-rebind` directory at final shell step through recoverable trash. Verify exactly one Projects/research-tool* directory remains. Current chat's shell binding requires reopening canonical for later turns; do not mutate runtime SQLite to hide it.

### Task 6: Integration and live acceptance

- [ ] Run complete `python -B -m pytest -q -p no:cacheprovider --basetemp=<output>/pytest --junitxml=<output>/pytest.xml research_tool/tests`, Ruff, git diff --check, bash -n scripts/setup.sh and uv lock --check --offline.
- [ ] Run scripts/smoke_e2e.py in ignored output; inspect actual STOP_SUCCESS, replenishment and zero-model resume receipt.
- [ ] Recheck test provider via real client text/JSON/stream and masked config output, with explicit request and total budgets.
- [ ] Execute small real-network CLI run with genuine source bodies and model calls; inspect exit code, runtime, STOP_SUCCESS, file hashes, final report contents and resume. No success claim from logs alone.
- [ ] For medical research, acquire directly relevant original cohorts; distinguish target disease from quantitative hypofibrinogenemia, patient count from challenge-event count, prophylaxis from natural outcomes, adjusted effects from unadjusted percentages. If numerical contribution cannot be supported, state non-estimability instead of fabricating.
- [ ] Independently review diff and live acceptance artifacts; fix evidence-backed issues. No commit, push or PR merge without a separate user request.

## Acceptance boundaries

Offline fixtures prove runtime behavior, not external quality. Real text/JSON probes prove compatibility, not context capacity. A live STOP_SUCCESS receipt proves product checks, not medical truth. Historical 52.77 is not reproduced by this task. All remaining limitations are reported explicitly.
