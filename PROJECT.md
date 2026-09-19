# Project: research-tool 9-Stage Native Pipeline Migration

## Architecture
The research-tool production pipeline is migrated from the legacy 5/6-stage model to the native 9-stage closed-loop architecture:
$$\text{① Collect} \longrightarrow \text{② Clean} \longrightarrow \text{③ Extract} \longrightarrow \text{④ Knowledge} \longrightarrow \text{⑤ Inspect} \longrightarrow \text{⑥ Targeted} \longrightarrow \text{⑦ Merge} \longrightarrow \text{⑧ QGate} \longrightarrow \text{⑨ Report}$$

- **Core Dispatcher**: `ResearchPipeline` in `research_tool/application/pipeline.py` and SDK `research()` in `research_tool/__init__.py`.
- **Closed Loop Subsystem**: ⑤ Inspect scans the graph for contradictions and structural gaps; ⑧ QGate evaluates thresholds; if `CONTINUE`, ⑥ Targeted generates de-anchored queries and ⑦ Merge executes Compare-And-Swap (CAS) atomic updates back into ④ Knowledge, closing the self-healing loop.
- **Identity & Normalization Layer**: `rt_identity_adapter.py` orchestrates URL/hash normalization with dual-arm support: Rust `rt-identity` subprocess for performance and a 100% bit-identical `PythonIdentityEngine` for zero-binary resilience.
- **Multimodal & Downstream Integration**: `VideoPipeline` and `PdfIngestor` deposit into `raw/`; `TalkLinker` enriches via CAS merge; downstream `wiki-stage` consumes `report.md`, `tree/00-主表.md`, `sources.json`, and `run-summary.json` with strict Citation Coverage 1.0.

---

## Feature Inventory
Every feature identified during the Survey phase is enumerated below with its assigned milestone:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F01 | Native 9-Stage Dispatcher | Rewrite `ResearchPipeline.run()` and `.stream()` to natively drive stages ①-⑨ | M1 | Survey E1 |
| F02 | Python SDK Native Alignment | Update `research()` and `create_pipeline()` to natively invoke 9-stage pipeline | M1 | Survey E1 |
| F03 | Legacy Dispatch & Wipe Purge | Eliminate legacy 5/6-stage dispatch, directory wiping (`_invalidate_after_collect`), and backward loops | M1 | Survey E1 |
| F04 | Shadow Sidecar & Flags Purge | Delete `flags.py`, `shadow.py`, `shadow_e2e.py`, and obsolete flags (`nine_loop.enabled`, `deepen_as_strategy`) | M1 | Survey E1 |
| F05 | Atomic CAS & Checkpointing | Implement `ChainState` atomic commits (`artifacts/{stage}.json` + `state.json`) with SHA-256 verification | M1 | Survey E1 |
| F06 | Stage Literal & Alias Support | Update `StageName` literal to 9 stages, supporting canonical aliases (`network` $\leftrightarrow$ `knowledge`, `gate` $\leftrightarrow$ `qgate`) | M2 | Survey E2 |
| F07 | Domain Config Schema Modernization | Add `InspectConfig`, `TargetedConfig`, `QGateConfig`, `BudgetLeaseConfig`; remove `DeepenConfig` & `NineLoopConfig` | M2 | Survey E2 |
| F08 | CLI Options Modernization | Remove `--profile-iterations`, `--max-backward-rounds`; add `--qgate-max-high`, `--qgate-max-total`, `--max-targeted-rounds`, `--max-targeted-queries`, `--inspect-rules` | M2 | Survey E2 |
| F09 | CLI Strict & Status Alignment | Update `RESEARCH_AGENT_STRICT` check to mandate 9 stages; update `status` command for 9-stage artifacts | M2 | Survey E2 |
| F10 | WebUI Modernization | Replace obsolete sliders with QGate/Targeted controls, update Chinese labels, enforce path safety and strict mode | M2 | Survey E2 |
| F11 | Config Template Modernization | Update `docs/config.example.yaml` with native 9-stage blocks and purge commented feature flags | M2 | Survey E2 |
| F12 | Multimodal Ingest Compatibility | Update `PipelineTrigger` in `pipeline_adapter.py` to 9 stages; update `clean_min.py` `ALLOWED_SCHEMES` for `file://` | M3 | Survey E3 |
| F13 | Non-Destructive TalkLinker | Integrate `TalkLinker` with Stage ⑦ Merge as an incremental evidence enricher using CAS | M3 | Survey E3 |
| F14 | Downstream Wiki-Stage Deliverables | Materialize `report.md`, `tree/00-主表.md`, `sources.json`, `run-summary.json` with Citation Coverage 1.0 | M3 | Survey E3 |
| F15 | Pure-Python Identity Fallback | Implement `PythonIdentityEngine` in `rt_identity_adapter.py` matching `oracle.v1.jsonl` test vectors | M3 | Survey E3 |
| F16 | Legacy Test Modernization | Modernize 65 legacy pipeline tests (`test_pipeline_*.py`, `test_run_modes.py`, `test_nine_loop_assembly.py`) | M4 | Survey E3 |
| F17 | Nine-Loop Contract Test Porting | Port 15 contract test suites from refactor workspace into `research_tool/tests/` | M4 | Survey E3 |
| F18 | Full Pytest Suite Regression | Verify 100% pass across all 876+ tests in target conda environment with 0 failures | M4 | Survey E3 |
| F19 | E2E Requirement Test Pass | Pass 100% of opaque-box E2E test suite (Tiers 1-4) produced by E2E Testing Track | M5 | User Request |
| F20 | Adversarial Coverage Hardening | White-box Challenger loop for edge case, boundary, and concurrency stress testing (Tier 5) | M5 | User Request |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| **E2E** | E2E Testing Track | Create comprehensive opaque-box test suite (Tiers 1-4) covering all 20 features; publish `TEST_READY.md` | None | DONE |
| **M1** | Pipeline Core & Deprecation | Native 9-stage loop in `ResearchPipeline` and SDK, purge legacy code paths, shadow sidecars, and dead flags | None | DONE |
| **M2** | Config, CLI & WebUI Alignment | Domain models, `domain/config.py`, CLI commands, WebUI controls, strict security guards, example YAML | M1 | DONE |
| **M3** | Ingest, Wiki & Rust Fallback | Multimodal `PdfIngestor`/`VideoPipeline` integration, `TalkLinker` CAS merge, `wiki-stage` deliverables, `PythonIdentityEngine` | M1, M2 | IN_PROGRESS |
| **M4** | Test Suite Modernization | Modernize legacy 5-stage tests, port contract suites, achieve 100% pass in target environment | M1, M2, M3 | PLANNED |
| **M5** | Final E2E Pass & Hardening | Pass 100% E2E test suite (Tiers 1-4), execute Tier 5 adversarial coverage hardening | E2E, M4 | PLANNED |

---

## Interface Contracts

### 1. Common Stage Envelope (Protocol v1)
All 9 stages communicate through immutable JSON envelopes:
```json
{
  "v": 1,
  "run_id": "run-20260917-uuid",
  "stage": "collect|clean|extract|knowledge|inspect|targeted|merge|qgate|report",
  "request_id": "stage:001",
  "idempotency_key": "64_hex_sha256_of_inputs",
  "budget_lease": {
    "lease_id": "lease-uuid",
    "tokens_max": 1000000,
    "cost_max": 10.0,
    "wall_s_max": 600.0,
    "search_calls_max": 50,
    "issued_at": "ISO8601",
    "expires_at": "ISO8601"
  },
  "result": { ... },
  "error": null
}
```

### 2. Stage Aliases
Canonical names and supported aliases:
- Stage 4: `knowledge` (canonical domain name) $\longleftrightarrow$ `network` (internal contract name)
- Stage 8: `qgate` (canonical domain name) $\longleftrightarrow$ `gate` (internal contract name)

### 3. Compare-And-Swap (CAS) Merge Contract
- Input: `prev_digest` (digest of current network graph) and `responses` (delta facts from targeted search).
- Invariant: If `graph_digest(network) != prev_digest`, raise CAS mismatch and refresh state. Conflicting viewpoints are maintained as `conflict_version`.

### 4. Downstream Wiki-Stage Contract
- `wiki_stage.py` requires:
  - `report.md`: Citation Coverage = 1.0 (unverified claims dropped into `dropped_claims`).
  - `tree/00-主表.md`: Root outline index for the knowledge tree.
  - `sources.json`: Complete source manifest (`url`, `fetchedAt`, `content_hash`).
  - `run-summary.json`: Run summary with execution metrics and status.

### 5. Identity Normalization Contract
- `rt_identity_adapter.py`: Protocol v1 over stdio JSONL or in-process `PythonIdentityEngine`.
- Outputs must be bit-identical across Rust and Python engines, matching `oracle.v1.jsonl`.

---

## Code Layout & Write Ownership

| Module / Directory | Primary Responsibility | Milestone Owner |
|---|---|---|
| `research_tool/application/pipeline.py` | Native 9-stage pipeline dispatcher & lifecycle | M1 |
| `research_tool/__init__.py` | Python SDK `research()` and `create_pipeline()` | M1 |
| `research_tool/nine_loop/flags.py`, `shadow.py`, `shadow_e2e.py` | Deprecated scaffolding (TO BE DELETED) | M1 |
| `research_tool/domain/models.py` | StageName literal, Config models (`Inspect`, `Targeted`, `QGate`) | M2 |
| `research_tool/domain/config.py` | Config loading, mode presets, override parsing | M2 |
| `research_tool/presentation/cli.py` | CLI commands (`run`, `status`, `config`), options, strict mode | M2 |
| `research_tool/presentation/webui.py` | WebUI tabs, event streaming, strict mode, path safety | M2 |
| `docs/config.example.yaml` | Production configuration documentation | M2 |
| `research_tool/nine_loop/clean_min.py` | Allowed schemes expansion (`file://`) | M3 |
| `research_tool/infrastructure/ingest/pipeline_adapter.py` | Multimodal ingest pipeline trigger (9 stages) | M3 |
| `research_tool/application/talk_linker.py` | Non-destructive CAS merge enrichment | M3 |
| `research_tool/nine_loop/rt_identity_adapter.py` | Pure-Python identity engine & fallback logic | M3 |
| `research_tool/tests/` (existing suite) | Modernized test suite (65+ tests updated) | M4 |
| `tests/e2e/` | Opaque-box E2E test suite (Tiers 1-4) | E2E Testing Track |
