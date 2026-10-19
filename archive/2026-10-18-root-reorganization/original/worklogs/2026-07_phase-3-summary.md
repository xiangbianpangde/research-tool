# Phase 3 Summary (2026-07)

Durable record of the Phase 3 optimization arc (Rounds R1–R11). All work merged to
`master` and pushed. Production code frozen after R11; this round (R12) is docs-only.

## Rounds R1–R11

Round-to-commit mapping for R1–R8 is inferred from commit messages (those round
handovers predate the current session context); R9–R11 are from this session.

| Round | Commit(s) | Theme |
|-------|-----------|-------|
| R1 | `e181cd6`, `576d646`, `02c9de6` | Package rename (`src`→`research_tool`); gitleaks CI + ADR 0002 (rotate/scrub leaked DeepSeek key); repo-wide ruff sweep + docs exclude. |
| R2 | `aa2b8a3` | SSRF guard (`common/url_guard.py`) blocking private/link-local/cloud-metadata URLs in fetcher/collector. |
| R3 | `6197b65` | Docs alignment — CLAUDE.md/STATUS.md reconciled with Phase 2 drift + 3 code rounds. |
| R4 | `d74dbe0`, `6380b2a` | Error-path correctness — `resolve_exit_code` must not return 0 for 404/400 hints; config error messages point at `docs/config.example.yaml`. |
| R5 | `f189f45`, `b2954d0` | DeepseekClient removal (M-006 vestige) + ADR 0003 (supersede). |
| R6 | `7d864e4`, `24ffe07` | defusedxml migration (S314) for arxiv/google_news XML parsing + tech-debt update. |
| R7 | (no commit) | Second-pass review — surfaced backward-loop zero-coverage gap + M-010 error-code disconnect; drove R8–R11. |
| R8 | `4d03737`, `0695991`, `56c27a1` | Video summarizer fix (MiniMax permanent default, remove placeholder stub) + remove dead dep `arxiv>=2.1` + docs. |
| R9 | `1e6f268`, `5c32063`, `3392d70` | M-010 CLI wiring (`format_error` + `resolve_exit_code` → `_fail_video_ingest`); registered `E_VID_003_PIPELINE_FAIL` (12→13 codes). |
| R10 | `09cc763`, `d4a3ab8` | M-010 reconciliation — 13 legacy codes registered (13→26) + 14 register/raise mismatches aligned + 4 raise semantic fixes (yt-dlp-missing 500→403, cookie-missing 400→401). |
| R11 | `60e52c1` | Backward-loop tests (P2-3) — `_invalidate_after_collect` + `_recollect` + 5 `stream()` control-flow cases + `.deepen_done` contract (8 tests; was zero coverage). |

## ADRs

- **0001** — 六阶段管道文件系统通信 (pre-existing, FP01): stages communicate only via the filesystem; enables interrupt/resume/debug.
- **0002** — rotate-and-scrub-leaked-deepseek-key (R1): rotated the compromised key, scrubbed git history, added gitleaks CI to prevent recurrence.
- **0003** — supersede-m006-deepseekclient (R5): removed the vestigial `DeepseekClient`; `from_config` routes deepseek to `OpenAILLMClient` (OpenAI-compatible).

## Deferred backlog

Recorded in `STATUS.md` tech-debt table. Most worth doing next:
**vestigial register_error override preservation** (P2) and **commitlint enforcement** (closes 03-Git 🟡).

### P2
- **vestigial register_error override preservation**: make `_fail_video_ingest` use the
  ingest modules' `ErrorRecord` overrides (scene/cause/suggestion) instead of defaults.
  Involves state storage, concurrency thread-safety, state cleanup — a dedicated design
  round, not a wrap-up. Completes the M-010 line (R9 wiring → R10 registration → this
  round preserves overrides). Currently `_fail_video_ingest` re-calls `register_error`
  with defaults, so the rich overrides in ingest modules are discarded.

### P3 / low
- 4 dead error-code constants (`E_VID_PIPELINE_FAIL`, `E_LLM_002_CHAPTERS_FALLBACK`,
  `E_NS_001_YAML_PARSE_FAIL`, `E_NS_002_SCREENSHOT_MISSING`): defined + exported but
  never raised. Remove or register. (R10 audit finding.)
- P3-1: extend the 01-架构 sanctioned-deviation list for `cli.py:33`, `webui.py:78`
  (presentation→infrastructure imports).
- P3-2: `ocr_cmd` `--` separator (filename argument injection; very low risk).
- `deepen.run` split (PLR0915): noqa'd; pure refactor.
- commitlint enforcement: config staged (`meta/commitlint.config.js`); pre-commit/CI
  hook not wired. Closes 03-Git 🟡.
- pytest-cov: install + addopts + baseline. Closes 05-测试 coverage gap.
- Configurable SSRF block list (`198.18.0.0/15` currently exempted for local DNS proxy):
  opt-in for non-proxy environments.
- `deliverable.md` + `deliverable-track-*.md`: pre-rename `src/` paths; classify
  (tracked → update; untracked → gitignore).
- Bridge script `--use-minimax-summary` flag redundancy: MiniMax is now the core default;
  simplify or remove the flag.

## Security / correctness conclusions

- **R7 second-pass review found no P0 issues.** Findings were coverage gaps and the
  M-010 disconnect (both since closed: R11 tests + R10 registration).
- **Subprocess calls all use list-form (zero `shell=True`)** — no shell injection surface.
- **Cache keys and URL fingerprints are sha256-derived**; output filenames are sanitized
  (`slugify` / `_sanitize_filename`) — no unsanitized user input in filesystem paths.
- **Cookie files require `0o600` perms** (Unix; `CookieInjector.validate_perms`) —
  cookies treated as login credentials, never logged.
- **Backward loop preserves `raw/`** — `_invalidate_after_collect` removes only
  `clean/`/`extracted/`/`tree/` + `.deepen_done` + `report.*`; raw history (incl.
  newly-recollected material) is retained across backward rounds. Pinned by R11 tests.

## Final state

- **Tests**: 454 passed / 5 skipped.
- **Error codes**: 26 (M-010 fully functional: 3-section `format_error` + arbitrated
  `resolve_exit_code`; all 56 raise sites resolve via `lookup_code`).
- **Search backends**: 12 (+ CachingBackend wrapper).
- **LLM providers**: 5 (DeepSeek/OpenAI/Anthropic/Ollama/MiniMax → 2 client classes).
- **ADRs**: 0001–0003.
- **Spec status**: 01-架构 🟡, 02-代码 ✅, 03-Git 🟡, 05-测试 ✅, 06-文档 ✅.
