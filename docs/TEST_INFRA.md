# TEST_INFRA — E2E Testing Infrastructure Specification

**Status**: PUBLISHED  
**Version**: 1.0.0  
**Author**: E2E Test Architect & Writer (`test_writer_e2e`)  
**Target Environment**: `/opt/anaconda3/envs/research-tool` (Python 3.11.15, Pytest 9.1.1)  
**Project**: `research-tool` 9-Stage Native Pipeline Migration  
**Date**: 2026-09-17  

---

## 1. Executive Summary & Testing Philosophy

This document specifies the end-to-end (E2E) testing infrastructure for the `research-tool` 9-stage closed-loop architecture:
$$\text{① Collect} \longrightarrow \text{② Clean} \longrightarrow \text{③ Extract} \longrightarrow \text{④ Knowledge} \longrightarrow \text{⑤ Inspect} \longrightarrow \text{⑥ Targeted} \longrightarrow \text{⑦ Merge} \longrightarrow \text{⑧ QGate} \longrightarrow \text{⑨ Report}$$

### 1.1 Opaque-Box & Requirement-Driven Principle
The E2E testing track adopts a strict **opaque-box** testing methodology:
- Tests interact with the system strictly across public external boundaries:
  1. **CLI Subprocess Boundary**: `research run`, `status`, `config`, `ingest-pdf`, `wiki-stage`, `publish-wiki`.
  2. **Python SDK Boundary**: `research_tool.research()`, `research_tool.create_pipeline()`.
  3. **Stage Contract Envelope Boundary**: Protocol v1 JSON envelopes (`artifacts/{stage}.json` + `state.json`).
  4. **Deliverables & Artifact Boundary**: `report.md`, `tree/00-主表.md`, `sources.json`, `run-summary.json`, and wiki packages.
- **Zero-Facade Guarantee**: Tests never assert trivial truth or bypass real logic. Every test exercises real code paths and asserts observable side-effects.
- **Progressive Testability & Defect Escalation**: Features under active development are verified against target specifications; implementation gaps are authoritatively tracked and escalated to implementing agents.

---

## 2. Test Architecture & Four-Tier Hierarchy

The test suite is structured into four distinct verification tiers under `tests/e2e/`:

```
tests/e2e/
├── conftest.py                             # Global fixtures, mock providers, CLI runner helpers
├── tier1_features/                         # Tier 1: Feature Coverage (>=5 test cases per feature)
│   ├── test_f01_native_dispatcher.py       # F01: Native 9-Stage Dispatcher
│   ├── test_f02_sdk_alignment.py           # F02: Python SDK Native Alignment
│   ├── test_f03_legacy_purge.py            # F03: Legacy Dispatch & Wipe Purge
│   ├── test_f04_shadow_flags_purge.py      # F04: Shadow Sidecar & Flags Purge
│   ├── test_f05_atomic_cas_checkpoint.py   # F05: Atomic CAS & Checkpointing
│   ├── test_f06_stage_literal_aliases.py   # F06: Stage Literal & Alias Support
│   ├── test_f07_config_schema.py           # F07: Domain Config Schema Modernization
│   ├── test_f08_cli_options.py             # F08: CLI Options Modernization
│   ├── test_f09_cli_strict_status.py       # F09: CLI Strict & Status Alignment
│   ├── test_f10_webui_modernization.py     # F10: WebUI Modernization
│   ├── test_f11_config_template.py         # F11: Config Template Modernization
│   ├── test_f12_multimodal_ingest.py       # F12: Multimodal Ingest Compatibility
│   ├── test_f13_talk_linker_merge.py       # F13: Non-Destructive TalkLinker
│   ├── test_f14_downstream_wiki.py         # F14: Downstream Wiki-Stage Deliverables
│   ├── test_f15_python_identity_fallback.py# F15: Pure-Python Identity Fallback
│   ├── test_f16_legacy_test_modernization.py# F16: Legacy Test Modernization
│   ├── test_f17_nine_loop_contract.py      # F17: Nine-Loop Contract Test Porting
│   ├── test_f18_full_regression.py         # F18: Full Pytest Suite Regression
│   ├── test_f19_e2e_pass.py                # F19: E2E Requirement Test Pass
│   └── test_f20_adversarial_hardening.py   # F20: Adversarial Coverage Hardening
├── tier2_boundaries/                       # Tier 2: Boundary & Corner Cases (>=5 test cases per feature)
│   ├── test_b01_dispatcher_boundaries.py   # B01: Dispatcher limits, errors, empty stages
│   ├── test_b02_sdk_boundaries.py          # B02: SDK empty topic, illegal paths, missing config
│   ├── test_b03_legacy_purge_boundaries.py # B03: Preserved files, permission traps, no-op passes
│   ├── test_b04_shadow_flags_boundaries.py # B04: Deprecated flag injection, invalid flag combos
│   ├── test_b05_cas_checkpoint_boundaries.py# B05: Checkpoint tamper, disk full, corrupted digest
│   ├── test_b06_alias_boundaries.py        # B06: Mixed alias names, casing, unsupported aliases
│   ├── test_b07_config_schema_boundaries.py# B07: Negative thresholds, malformed YAML, extra keys
│   ├── test_b08_cli_options_boundaries.py  # B08: Extreme flags, incompatible CLI options
│   ├── test_b09_strict_status_boundaries.py# B09: Strict mode violations, empty artifacts status
│   ├── test_b10_webui_boundaries.py        # B10: WebUI path traversal, malicious injection
│   ├── test_b11_config_template_boundaries.py# B11: Template syntax corruption, missing blocks
│   ├── test_b12_multimodal_boundaries.py   # B12: Corrupted PDF, 0-byte video, forbidden schemes
│   ├── test_b13_talk_linker_boundaries.py  # B13: Low title similarity, missing talk candidates
│   ├── test_b14_downstream_wiki_boundaries.py# B14: Missing sources, dropped claims, secret detection
│   ├── test_b15_identity_fallback_boundaries.py# B15: Private IPs, invalid UTF-8, malformed queries
│   ├── test_b16_legacy_test_boundaries.py  # B16: Obsolete test fixture handling
│   ├── test_b17_contract_boundaries.py     # B17: Expired budget leases, duplicate stage IDs
│   ├── test_b18_regression_boundaries.py   # B18: Timeout handling, proxy failure recovery
│   ├── test_b19_e2e_pass_boundaries.py     # B19: High finding density, max targeted limit reached
│   └── test_b20_adversarial_boundaries.py  # B20: Path injection, unicode attacks, resource stress
├── tier3_combinations/                     # Tier 3: Pairwise Combinatorial Tests
│   └── test_pairwise_combinations.py       # Cross-feature interactions and state transitions
└── tier4_scenarios/                        # Tier 4: Real-World Application Scenarios
    └── test_real_world_scenarios.py        # Comprehensive multi-stage real-world research workflows
```

---

## 3. Authoritative Verification Oracles & Contracts

Every test case derives its expected values from authoritative architectural contracts:

### 3.1 Common Stage Envelope (Protocol v1)
All 9 stages must emit an immutable JSON envelope:
```json
{
  "v": 1,
  "run_id": "<string>",
  "stage": "collect|clean|extract|knowledge|inspect|targeted|merge|qgate|report",
  "request_id": "<string>",
  "idempotency_key": "<sha256_hex_64>",
  "budget_lease": {
    "lease_id": "<string>",
    "tokens_max": 1000000,
    "cost_max": 10.0,
    "wall_s_max": 600.0,
    "search_calls_max": 50,
    "issued_at": "<ISO8601>",
    "expires_at": "<ISO8601>"
  },
  "result": { ... },
  "error": null
}
```
**Verification Oracle**: Envelopes must conform to Protocol v1 schema; on error, `error` must carry safe typed codes (`E_SCHEMA`, `E_LEASE_INVALID`, `E_IDEMPOTENCY_CONFLICT`) without leaking secrets.

### 3.2 Atomic CAS & Digest Checkpointing
- Invariant: `artifacts/{stage}.json` must be written atomically (`write_atomic` via PID-tagged temporary file + `os.replace`).
- Invariant: `state.json` must record `stages[stage] == sha256(artifacts/{stage}.json)` and append stage to `done`.
- Resume Invariant: If `stage in state.done` and input `idempotency_key` matches, stage re-execution is skipped. Any digest mismatch raises `E_STATE`.

### 3.3 Citation Coverage 1.0 Invariant
- Every statement synthesized into `report.md` must resolve to verified source citations.
- Invariant: Unverified hypotheses or orphan claims must be physically purged from the report and placed into `dropped_claims`.
- Invariant: $\text{Citation Coverage} = \frac{\text{Cited Claims}}{\text{Total Claims in Report}} = 1.0$.

### 3.4 Identity Engine Bit-Identical Oracle
- Invariant: URL and content normalization must match `crates/rt-identity/fixtures/oracle.v1.jsonl`.
- Zero-Binary Resilience: When Rust `rt-identity` is absent, `PythonIdentityEngine` must produce identical canonical locators, stable IDs, and deduplication decisions (`retain`, `alias`, `conflict_version`).

### 3.5 Downstream Wiki-Stage Package Contract
- Packaging into `.stage/` directory:
  - Directory permissions must be `0o555` (read + execute, no write).
  - File permissions for `manifest.json`, `archive/*.blob`, `report.md`, `source-manifest.md` must be `0o444` (read-only).
  - Secret scanning: Zero occurrences of private keys, bearer tokens, or API secrets.

---

## 4. Test Execution Infrastructure

### 4.1 Test Runner Command
The test suite is fully discovered and executed using the dedicated Python environment:

```bash
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/ -v
```

### 4.2 Selective Execution by Tier
```bash
# Tier 1: Feature Coverage
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/tier1_features/ -v

# Tier 2: Boundary & Corner Cases
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/tier2_boundaries/ -v

# Tier 3: Cross-Feature Combinations
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/tier3_combinations/ -v

# Tier 4: Real-World Application Scenarios
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/tier4_scenarios/ -v
```

### 4.3 Parallel & Fast Execution
```bash
/opt/anaconda3/envs/research-tool/bin/pytest tests/e2e/ -q --tb=short
```

---

## 5. Requirements Traceability Matrix (F01 - F20)

| Feature ID | Feature Name | Tier 1 Suite | Tier 2 Suite | Tier 3 Pairwise | Tier 4 Scenario |
|:---:|:---|:---|:---|:---:|:---:|
| **F01** | Native 9-Stage Dispatcher | `tier1_features/test_f01_*.py` | `tier2_boundaries/test_b01_*.py` | Yes | S1, S3, S4, S5 |
| **F02** | Python SDK Native Alignment | `tier1_features/test_f02_*.py` | `tier2_boundaries/test_b02_*.py` | Yes | S1, S3 |
| **F03** | Legacy Dispatch & Wipe Purge | `tier1_features/test_f03_*.py` | `tier2_boundaries/test_b03_*.py` | Yes | S2, S4 |
| **F04** | Shadow Sidecar & Flags Purge | `tier1_features/test_f04_*.py` | `tier2_boundaries/test_b04_*.py` | Yes | S3 |
| **F05** | Atomic CAS & Checkpointing | `tier1_features/test_f05_*.py` | `tier2_boundaries/test_b05_*.py` | Yes | S4 |
| **F06** | Stage Literal & Alias Support | `tier1_features/test_f06_*.py` | `tier2_boundaries/test_b06_*.py` | Yes | S1, S5 |
| **F07** | Domain Config Schema Modernization | `tier1_features/test_f07_*.py` | `tier2_boundaries/test_b07_*.py` | Yes | S5 |
| **F08** | CLI Options Modernization | `tier1_features/test_f08_*.py` | `tier2_boundaries/test_b08_*.py` | Yes | S4, S5 |
| **F09** | CLI Strict & Status Alignment | `tier1_features/test_f09_*.py` | `tier2_boundaries/test_b09_*.py` | Yes | S1, S4 |
| **F10** | WebUI Modernization | `tier1_features/test_f10_*.py` | `tier2_boundaries/test_b10_*.py` | Yes | S2 |
| **F11** | Config Template Modernization | `tier1_features/test_f11_*.py` | `tier2_boundaries/test_b11_*.py` | Yes | S3 |
| **F12** | Multimodal Ingest Compatibility | `tier1_features/test_f12_*.py` | `tier2_boundaries/test_b12_*.py` | Yes | S1, S2 |
| **F13** | Non-Destructive TalkLinker | `tier1_features/test_f13_*.py` | `tier2_boundaries/test_b13_*.py` | Yes | S2 |
| **F14** | Downstream Wiki-Stage Deliverables | `tier1_features/test_f14_*.py` | `tier2_boundaries/test_b14_*.py` | Yes | S1, S5 |
| **F15** | Pure-Python Identity Fallback | `tier1_features/test_f15_*.py` | `tier2_boundaries/test_b15_*.py` | Yes | S3 |
| **F16** | Legacy Test Modernization | `tier1_features/test_f16_*.py` | `tier2_boundaries/test_b16_*.py` | Yes | S4 |
| **F17** | Nine-Loop Contract Test Porting | `tier1_features/test_f17_*.py` | `tier2_boundaries/test_b17_*.py` | Yes | S3, S5 |
| **F18** | Full Pytest Suite Regression | `tier1_features/test_f18_*.py` | `tier2_boundaries/test_b18_*.py` | Yes | S1-S5 |
| **F19** | E2E Requirement Test Pass | `tier1_features/test_f19_*.py` | `tier2_boundaries/test_b19_*.py` | Yes | S1-S5 |
| **F20** | Adversarial Coverage Hardening | `tier1_features/test_f20_*.py` | `tier2_boundaries/test_b20_*.py` | Yes | S3, S4, S5 |

---

## 6. Real-World Application Scenarios (Tier 4)

1. **Scenario 1 — Academic Multimodal Research Pipeline**
   - Flow: Ingest local PDF literature via `ingest-pdf` $\to$ emit `file://` sources $\to$ execute 9 stages $\to$ generate `tree/00-主表.md` and `report.md` with Citation Coverage 1.0 $\to$ build immutable wiki-stage package.
2. **Scenario 2 — Video Lecture & TalkLinker CAS Synthesis**
   - Flow: Research topic with paper references $\to$ `TalkLinker` discovers matching YouTube/Bilibili conference talks $\to$ CAS-merges talk transcripts into Stage ④ Knowledge $\to$ verifies zero destruction of existing artifacts.
3. **Scenario 3 — Zero-Binary Offline Investigation**
   - Flow: System operates in air-gapped environment without Rust `rt-identity` binary $\to$ triggers `PythonIdentityEngine` fallback $\to$ normalizes URLs, checks IP blocks, deduplicates sources, and drives full 9 stages deterministically.
4. **Scenario 4 — Mid-Flight Interruption & Idempotent Resume**
   - Flow: Pipeline interrupted after Stage ④ $\to$ re-invoked with `--resume` $\to$ verifies stages ①-④ skipped via SHA-256 state matching $\to$ stages ⑤-⑨ completed without redundant work.
5. **Scenario 5 — Autonomous Gap Discovery, Targeted Search & Self-Healing**
   - Flow: Collect conflicting statements $\to$ Stage ⑤ Inspect flags high-severity contradiction $\to$ Stage ⑧ QGate issues `CONTINUE` $\to$ Stage ⑥ Targeted searches de-anchored query $\to$ Stage ⑦ CAS merges resolution $\to$ QGate threshold passes $\to$ clean report materialized.
