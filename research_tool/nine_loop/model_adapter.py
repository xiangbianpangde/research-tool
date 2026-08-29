"""Model adapter — protocol R3 fallback-chain client (P5 baseline run).

OpenAI-compatible chat-completions client for the R3 chain position
(fallback: `deepseek-v4-flash` via api.b.ai). stdlib-only (urllib).

Security/protocol discipline:
  - API key read ONLY from owner-side env (DEEPSEEK_V4_API_KEY); never
    logged, never persisted, never echoed in errors (redaction enforced)
  - typed faults only (ModelFault with contract-style frame); no bare raises
  - budget accounting: usage tokens + estimated cost returned per call
  - timeout + single retry on transient transport errors; auth failure is a
    run-failure signal (no model switch inside the adapter — chain switching
    is the orchestrator's decision per protocol R3)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

PROTOCOL = "R3"
CHAIN_POSITION = "fallback"
MODEL_ID = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.b.ai/v1"
# api.b.ai sits behind Cloudflare bot protection (error 1010 on default
# urllib signature); a browser-like UA is required for the API path.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
              "Safari/537.36")
TIMEOUT_S = 60.0
RETRY_DELAY_S = 3.0

# --- C1: HTTP 429 rate-limit exponential backoff (PROPOSED params) -------
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_RETRIES = 4   # 5 total attempts
BACKOFF_MAX_DELAY_S = 30.0
# Retry-After 头尊重：有头 → delay = max(头值, 计算值)（见 _backoff_delay）

E_NO_KEY = "model.E_NO_KEY"
E_TRANSPORT = "model.E_TRANSPORT"
E_AUTH = "model.E_AUTH"
E_HTTP = "model.E_HTTP"
E_SCHEMA = "model.E_SCHEMA"

# rough published-rate estimate used ONLY for ledger accounting when the
# provider does not return cost — never claimed as authoritative pricing
EST_COST_PER_1K_TOKENS = 0.0


class ModelFault(Exception):
    """Typed fault carrying a contract-style frame (redacted)."""

    def __init__(self, code: str, safe_message: str,
                 http_status: int | None = None):
        self.code = code
        self.safe_message = safe_message
        self.http_status = http_status
        super().__init__(safe_message)


def redact(text: str) -> str:
    """Defense-in-depth: strip any key-shaped token from diagnostics."""
    if not text:
        return text
    out = text
    for env_name in ("DEEPSEEK_V4_API_KEY", "OPENROUTER_API_KEY"):
        v = os.environ.get(env_name, "")
        if v and v in out:
            out = out.replace(v, "[REDACTED]")
    return out


def params_hash(model: str, messages: list[dict[str, Any]],
                temperature: float, max_tokens: int) -> str:
    basis = json.dumps({"model": model, "messages": messages,
                        "temperature": temperature, "max_tokens": max_tokens},
                       sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class ModelCallResult:
    __slots__ = ("content", "usage", "model", "latency_s", "params_sha",
                 "raw_id")

    def __init__(self, content: str, usage: dict[str, Any], model: str,
                 latency_s: float, params_sha: str, raw_id: str):
        self.content = content
        self.usage = usage
        self.model = model
        self.latency_s = latency_s
        self.params_sha = params_sha
        self.raw_id = raw_id

    def ledger_row(self) -> dict[str, Any]:
        u = self.usage
        return {"model": self.model, "params_sha": self.params_sha,
                "prompt_tokens": u.get("prompt_tokens"),
                "completion_tokens": u.get("completion_tokens"),
                "total_tokens": u.get("total_tokens"),
                "est_cost_usd": EST_COST_PER_1K_TOKENS,
                "latency_s": round(self.latency_s, 3), "raw_id": self.raw_id}


def _backoff_delay(attempt: int, retry_after: int | None) -> float:
    """C1: exponential backoff + full jitter, Retry-After respected.

    delay = min(base * 2**attempt + uniform(0, base), MAX_DELAY) unless the
    server provided a Retry-After header (then delay = max(header, computed)).
    """
    import random
    jitter = random.uniform(0.0, BACKOFF_BASE_S)
    computed = min(BACKOFF_BASE_S * (2 ** attempt) + jitter,
                   BACKOFF_MAX_DELAY_S)
    if retry_after is not None:
        return max(float(retry_after), computed)
    return computed


def chat(messages: list[dict[str, Any]], *, base_url: str = DEFAULT_BASE_URL,
         model: str = MODEL_ID, api_key_env: str = "DEEPSEEK_V4_API_KEY",
         temperature: float = 0.0, max_tokens: int = 2048,
         transport: Any = None,
         wall_budget_s: float | None = None) -> ModelCallResult:
    """One chat completion. transport=None → real urllib; tests inject a mock.

    C1: HTTP 429 → exponential backoff + jitter retry (up to
    BACKOFF_MAX_RETRIES retries; Retry-After respected; each retry's waiting
    counts against wall_budget_s and success tokens count toward the caller's
    token cap). Exhausted → typed E_HTTP fault (honest degradation).
    Raises ModelFault on any failure (typed; redacted)."""
    key = os.environ.get(api_key_env, "")
    if not key:
        raise ModelFault(E_NO_KEY,
                         f"{api_key_env} not set in environment "
                         "(owner-side registration required)")
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    psha = params_hash(model, messages, temperature, max_tokens)
    url = base_url.rstrip("/") + "/chat/completions"

    last_err: ModelFault | None = None
    attempt = 0
    while True:
        started = time.monotonic()
        retry_after: int | None = None
        try:
            if transport is not None:
                status, resp_body = transport(url, body, key)
            else:
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}",
                             "User-Agent": USER_AGENT,
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    status, resp_body = resp.status, resp.read()
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            retry_after = int(float(retry_after))
                        except ValueError:
                            retry_after = None
            latency = time.monotonic() - started
            if status == 429 and attempt < BACKOFF_MAX_RETRIES:
                # C1: backoff then retry (budget-gated)
                delay = _backoff_delay(attempt, retry_after)
                if wall_budget_s is not None and delay > wall_budget_s:
                    raise ModelFault(E_HTTP, "HTTP 429: wall budget "
                                             "insufficient for backoff",
                                     http_status=429)
                time.sleep(delay)
                attempt += 1
                continue
            if status < 200 or status >= 300:
                snippet = redact(resp_body[:200].decode("utf-8",
                                                        errors="replace"))
                code = E_AUTH if status in (401, 403) else E_HTTP
                raise ModelFault(code, f"HTTP {status}: {snippet}",
                                 http_status=status)
            data = json.loads(resp_body.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return ModelCallResult(content, usage, data.get("model", model),
                                   latency, psha,
                                   data.get("id", ""))
        except ModelFault:
            raise
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read()[:200].decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 429 and attempt < BACKOFF_MAX_RETRIES:
                ra = e.headers.get("Retry-After")
                try:
                    ra = int(float(ra)) if ra else None
                except ValueError:
                    ra = None
                delay = _backoff_delay(attempt, ra)
                if wall_budget_s is not None and delay > wall_budget_s:
                    raise ModelFault(E_HTTP, "HTTP 429: wall budget "
                                             "insufficient for backoff",
                                     http_status=429)
                time.sleep(delay)
                attempt += 1
                continue
            code = E_AUTH if e.code in (401, 403) else E_HTTP
            last_err = ModelFault(code,
                                  f"HTTP {e.code}: {redact(detail)}",
                                  http_status=e.code)
        except urllib.error.URLError as e:
            last_err = ModelFault(E_TRANSPORT,
                                  f"URLError: {redact(str(e.reason))}")
        except TimeoutError:
            last_err = ModelFault(E_TRANSPORT, "timeout")
        except (KeyError, json.JSONDecodeError) as e:
            raise ModelFault(E_SCHEMA,
                             f"response schema unexpected: {type(e).__name__}")
        if attempt >= BACKOFF_MAX_RETRIES:
            break
        # non-429 transient path: at most ONE retry (baseline semantics)
        if attempt >= 1:
            break
        time.sleep(RETRY_DELAY_S)
        attempt += 1
    raise last_err if last_err else ModelFault(E_TRANSPORT, "unknown")
