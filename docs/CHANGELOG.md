# Changelog

## [Unreleased]

### Added (P7 migration — RT-RF program)
- Optional nine-loop pipeline promoted to **default path** (P7-T9 default
  switch, owner-approved). Legacy six-stage pipeline retained as fallback:
  set `nine_loop.enabled: false` (kill-switch; byte-identical legacy,
  drill-proven). Rollback tag: `pre-p7-default-switch`.
- `nine_loop.*` config keys: enabled / gate_enabled / deepen_as_strategy /
  adapter_enabled / shadow_enabled / shadow_sample_rate / stages.*.
- verify_flags() single-source assertion wired into flag-ON assembly
  (loud fail on multi-source flags).
- arXiv fulltext fetch (HTML5 first, PDF fallback) + raw/_originals
  archival; extractor concurrency now config-driven.
- Shadow infrastructure: divergence manifests + sidecar isolation
  (canary C0–C4 evidence chain, 216 divergence records).

### Changed
- Default pipeline path: legacy six-stage → nine-loop (feature-flagged
  since P6; canary C0→C4 all PASS). **User-visible output contract note**:
  nine-loop outputs land under sidecar roots (shadow/<run_id>/) as of the
  current integration; any change to the user-visible output contract is a
  separate, future owner decision — NOT included in this switch.
