# ADR 0002 — Rotate and scrub leaked DeepSeek key

**Date:** 2026-07-05
**Status:** Accepted
**Supersedes:** Earlier (incomplete) key-leak remediation noted in worklogs.

## Context

A hardcoded DeepSeek-format `sk-…` key literal was committed at
`meta/AI重构笔记.md:319` (inside a "❌ before / ✅ after" pedagogy example),
entering history in commit `faf1b71` ("chore: baseline refactor research_tool/ -> src/").
The repository remote is public (`github.com/xiangbianpangde/research-tool`),
so the key was treated as compromised regardless of the history scrub.

Recon found exactly one occurrence in one tracked file, introduced in one commit
(`faf1b71`); all `config*.yaml` and `.env` are gitignored, so no secondary leak
vector existed. Local `master` was in sync with `origin/master` (solo, 33 commits).

## Decision

1. **Rotate** the key at the DeepSeek console (gate G1). This is the real fix —
   history scrubbing only reduces future casual discovery; rotation is what
   actually neutralizes a compromised key.
2. **Scrub** history with `git filter-repo --replace-text`, replacing the literal
   with `***REMOVED***` while preserving the pedagogy example's structure. Both an
   exact-literal rule and a defensive `regex:sk-[A-Za-z0-9]{20,}` rule were
   applied on a fresh mirror clone.
3. **Force-push** the rewritten `master` to origin with `--force-with-lease`
   (gate G2): `dcd9ef6 -> f8c46f8`. The `v0.1.1` tag pointed to a pre-leak
   commit (`8100280`) and needed no rewrite; origin had no tags at all, so no
   tag force-push was required.
4. **Prevent recurrence** via the existing gitleaks pre-commit hook
   (`.pre-commit-config.yaml`) plus a gitleaks CI workflow
   (`.github/workflows/gitleaks.yml`).

## Consequences

- All commit SHAs from `faf1b71` forward changed (e.g. `faf1b71` -> `be5ab41`,
  `dcd9ef6` -> `f8c46f8`). Collaborators and CI must re-clone.
- 33 commits preserved; 0 `sk-` occurrences remain across all reachable history
  (verified via `git grep` across `git rev-list --all` on both the mirror and
  the freshly-fetched origin).
- Existing clones and forks retain the old (secret-bearing) history; this ADR
  documents that they are stale and must be discarded.
- The key is presumed compromised; rotation is the control that closes the
  exposure.

## Caveat for future operators

`git filter-repo` rewrites history but **not** the working tree. After resyncing
a working repo with `git reset --soft <clean-ref>`, any file whose content was
scrubbed will appear as a *staged* modification (old secret content vs the clean
HEAD) and would re-leak on the next commit. Such files must be restored to the
clean HEAD version (`git checkout HEAD -- <file>`) before committing. In this
remediation, `meta/AI重构笔记.md` required that restore.

## Tools used

- `git filter-repo --replace-text` (not BFG, not `filter-branch`).
- Backup bundle created before rewrite and retained off-path.
- `--force-with-lease` (explicit expected SHA) on push.
- gitleaks 8.24.3 (pre-commit hook `rev: v8.18.0` + CI action).
