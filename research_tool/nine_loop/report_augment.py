"""C2 report augmentation — RT-RF-P5-C2-IMPL-RUN-01.

Augment the ⑨report_min template claims with model-synthesized semantic
claims via the existing model_adapter (deepseek-v4-flash, C1 backoff
inherited). report_min.py is unchanged. Single variable: report augmentation
only.

Flow: consume report result + evidence spans + gate verdict → build prompt
(PROPOSED from design, not tuned) → model_adapter call → parse structured
claims → validate (weight sum=1.0, source_ids ⊆ manifest, citation linkage)
→ merge into report result claims. Model failure → skip; template claims
retained as fallback with degraded marker.

stdlib-only; tests use mock transport (no real model calls).
"""

from __future__ import annotations

import json
import pathlib
import re

from typing import Any


from . import model_adapter as MA  # noqa: E402

# Prompt template (PROPOSED from design §3 — NOT tuned within C2)
PROMPT_TEMPLATE = (
    "You are an evaluation pipeline report generator. Given:\n"
    "1. Task question: {question}\n"
    "2. Evidence spans: {evidence}\n"
    "3. Gate verdict: {verdict}\n"
    "4. Expected dimensions: {dimensions}\n"
    "5. Available source_ids (use these ONLY): {source_ids}\n"
    "6. Instruction: Use the EXACT factual wording from the source text/evidence spans; avoid paraphrasing. Quote key nouns, proper names, numbers, and predicates directly from the source.\n"
    "Produce a JSON array of weighted claims:\n"
    '[{{"claim": "...", "weight": 0.X, "source_ids": ["ds.v1:..."]}}]\n'
    "- Sum of weights = 1.0\n"
    "- Each claim cites >=1 evidence source_id from the list above\n"
    "- Claims are factual, verifiable assertions\n"
    "- 2-4 claims per task\n"
    '- If evidence insufficient: say "INSUFFICIENT"')


class AugmentFault(Exception):
    """Typed fault carrying a safe message (no payloads)."""

    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def build_prompt(task: dict, evidence: str, verdict: str) -> str:
    dims = ", ".join(task.get("required_dimensions", [])) or "n/a"
    srcs = ", ".join(task.get("source_ids", [])) or "n/a"
    return PROMPT_TEMPLATE.format(question=task.get("question", ""),
                                  evidence=evidence or "n/a",
                                  verdict=verdict or "n/a",
                                  dimensions=dims,
                                  source_ids=srcs)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    # remove ```json ... ``` or ``` ... ``` wrappers
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_claims(model_output: str,
                 manifest_ids: set[str]) -> list[dict[str, Any]]:
    """Parse model output into validated weighted claims.

    Validates: JSON array shape; weight sum=1.0 (±1e-9); source_ids ⊆
    manifest; 1..10 claims. Raises AugmentFault on structural violation.
    """
    text = _strip_code_fence(model_output)
    if not text:
        raise AugmentFault("augment.E_PARSE", "empty model output")
    if "INSUFFICIENT" in text.upper() and not text.lstrip().startswith("["):
        raise AugmentFault("augment.E_INSUFFICIENT",
                           "model reported insufficient evidence")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AugmentFault("augment.E_PARSE",
                           f"model output not valid JSON: {e}")
    if not isinstance(data, list) or not (1 <= len(data) <= 10):
        raise AugmentFault("augment.E_SCHEMA",
                           "claims must be a JSON array of 1..10 items")
    total_w = 0.0
    for item in data:
        if not isinstance(item, dict) or "claim" not in item \
                or "weight" not in item or "source_ids" not in item:
            raise AugmentFault("augment.E_SCHEMA",
                               "each claim needs claim/weight/source_ids")
        if not isinstance(item["claim"], str) or not item["claim"].strip():
            raise AugmentFault("augment.E_SCHEMA", "claim text empty")
        if type(item["weight"]) not in (int, float) \
                or not (0.0 < float(item["weight"]) <= 1.0):
            raise AugmentFault("augment.E_SCHEMA", "weight out of range")
        srcs = item["source_ids"]
        if not isinstance(srcs, list) or not srcs:
            raise AugmentFault("augment.E_SCHEMA", "source_ids empty")
        for sid in srcs:
            if sid not in manifest_ids:
                raise AugmentFault("augment.E_SOURCE",
                                   f"source_id not in manifest: {sid}")
        total_w += float(item["weight"])
    if abs(total_w - 1.0) > 1e-9:
        raise AugmentFault("augment.E_WEIGHT",
                           f"weight sum {total_w:.4f} != 1.0")
    return data


def augment_report(report_result: dict[str, Any], task: dict,
                   manifest_ids: set[str], evidence: str,
                   *, transport: Any = None,
                   wall_budget_s: float | None = None,
                   max_tokens: int = 4096) -> dict[str, Any]:
    """Augment report result with model-synthesized claims.

    Returns the (possibly merged) report result dict with an added
    `augment` field describing outcome. Never raises for model failure:
    on failure the template claims are retained with degraded marker.
    """
    verdict = (report_result.get("verdict") or "n/a")
    prompt = build_prompt(task, evidence, verdict)
    try:
        result = MA.chat([{"role": "user", "content": prompt}],
                         max_tokens=max_tokens,
                         transport=transport,
                         wall_budget_s=wall_budget_s)
    except MA.ModelFault as f:
        out = dict(report_result)
        out["augment"] = {"status": "degraded", "reason": "model_fault",
                          "code": f.code,
                          "safe_message": f.safe_message,
                          "model_claims": None}
        return out

    try:
        claims = parse_claims(result.content, manifest_ids)
    except AugmentFault as f:
        # C6: E_PARSE only — retry once with a simplified format constraint
        # (structural output constraint, NOT prompt wording tuning).
        # E_INSUFFICIENT is NEVER retried (evidence unchanged; refusal is
        # honest) and partial-claim fallback is NOT used (anti-fabrication).
        if f.code == "augment.E_PARSE":
            retry_prompt = prompt + (
                "\nOutput MUST be a JSON array only. No explanation text.")
            try:
                retry = MA.chat([{"role": "user",
                                 "content": retry_prompt}],
                                max_tokens=max_tokens,
                                transport=transport,
                                wall_budget_s=wall_budget_s)
                claims = parse_claims(retry.content, manifest_ids)
            except MA.ModelFault as rf:
                out = dict(report_result)
                out["augment"] = {"status": "degraded",
                                  "reason": "model_fault",
                                  "code": rf.code,
                                  "safe_message": rf.safe_message,
                                  "model_claims": None}
                return out
            except AugmentFault as rf:
                out = dict(report_result)
                out["augment"] = {"status": "degraded",
                                  "reason": "validation_failed",
                                  "code": rf.code,
                                  "safe_message": rf.safe_message,
                                  "model_claims": None,
                                  "retried": True}
                return out
            # retry succeeded: claims validated (weight/source_ids gates)
            out = dict(report_result)
            out["augment"] = {
                "status": "augmented",
                "model": retry.model,
                "params_sha": retry.params_sha,
                "usage": retry.usage,
                "model_claims": claims,
                "template_claims": list(
                    out.get("report", {}).get("claims", [])),
                "retried": True,
            }
            return out
        out = dict(report_result)
        out["augment"] = {"status": "degraded",
                          "reason": "validation_failed",
                          "code": f.code, "safe_message": f.safe_message,
                          "model_claims": None}
        return out

    # merge: model claims replace template claims for evaluation purposes
    out = dict(report_result)
    out["augment"] = {
        "status": "augmented",
        "model": result.model,
        "params_sha": result.params_sha,
        "usage": result.usage,
        "model_claims": claims,
        "template_claims": list(out.get("report", {}).get("claims", [])),
    }
    return out
