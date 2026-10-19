# ADR 0003 — Supersede M-006 DeepseekClient; MiniMax is the permanent video summarizer

**Date:** 2026-07-06
**Status:** Accepted
**Supersedes:** The M-006 DeepseekClient design (DD-001:M-006 LLM 客户端, deepseek-v4-flash).

## Context

M-006 specified a dedicated `DeepseekClient` (in
`research_tool/infrastructure/llm/deepseek_client.py`) for DeepSeek-based video
summarization, with convenience helpers `summarize_transcript` and
`extract_chapters`. It was an adapter over the OpenAI protocol, defaulting to
`deepseek-v4-flash`.

The video pipeline was subsequently redesigned around a `summarizer_fn` injection
pattern: callers (the bridge / CLI) inject the summarizer at call time rather than
the pipeline hard-coding a DeepSeek dependency. MiniMax is now the permanent video
summarizer. As a result `DeepseekClient` was fully superseded:

- Its standard methods (`chat` / `chat_structured` / `stream`) duplicated
  `OpenAILLMClient`, which already serves the `deepseek` provider in production —
  `LLMClient.from_config` routes `deepseek` → `OpenAILLMClient`
  (`research_tool/infrastructure/llm/base.py:96`), default model `deepseek-chat`
  (`base.py:131`).
- Its video helpers (`summarize_transcript`, `extract_chapters`) had **no
  production caller** — grep-confirmed that only `test_deepseek_client.py`
  referenced them.
- It was exported from `infrastructure/llm/__init__.py` but was never part of the
  top-level public API (`research_tool/__init__.py.__all__`).

The general `deepseek` provider is unaffected and continues to work via
`OpenAILLMClient` with no behavior change to the standard pipeline.

## Decision

1. **Remove** `deepseek_client.py` and its test module `test_deepseek_client.py`
   (commit `f189f45`).
2. **Clean the export**: drop `DeepseekClient` from
   `infrastructure/llm/__init__.py` (`__all__` is now `LLMClient, LLMConfig,
   MockLLMClient`).
3. **Record MiniMax as the permanent video summarizer** — the `summarizer_fn`
   injection pattern is the canonical path; no client class is hard-wired to the
   video pipeline.
4. **Leave `from_config` routing unchanged** — `deepseek` → `OpenAILLMClient`
   remains the supported path for the general (non-video) pipeline.

## Consequences

- 16 tests removed with the dead code (suite: 423 → 407 passed). This is an
  intentional dead-code removal, not a regression — no production code imported
  `DeepseekClient`, so there were no collateral test failures.
- The M-006 spec section (`docs/plan/.../M-006/*` and any `deliverable.md`
  references) is now historical; this ADR records the supersession rather than
  rewriting those artifacts.
- Future video-summarizer changes should extend the `summarizer_fn` injection
  pattern, not reintroduce a provider-specific client class.
- Anyone who previously imported `DeepseekClient` from
  `research_tool.infrastructure.llm` must switch to `OpenAILLMClient` (via
  `LLMClient.from_config` with `provider="deepseek"`) for general LLM use, or to
  the injected `summarizer_fn` for video summarization.
