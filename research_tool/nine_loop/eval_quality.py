"""Quality baseline evaluator (PRE numbers) — RT-RF-P5-QUALITY-EVAL-01.

Extracts report claims from runs/baseline-r2-c1 task artifacts, normalizes
them, greedily best-matches against dev weighted gold claims, and computes
per-task precision/recall/F1 (claim-level, weight-aware) + citation coverage
+ aggregate means.

Honest framework: dev gold is single-annotator engineering-level gold, NOT
dual-blind gold (pilot-gold is the frozen dual-blind baseline for pilot
runs). These are PRE numbers, not evaluation conclusions.

Deterministic: greedy matching over sorted candidate pairs (no randomness);
double-run produces identical output. stdlib-only; read-only over runs/ and
dataset/.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNS = REPO / "runs/baseline-r2-c1/tasks"  # overridden below
DEV = REPO / "dataset/v1/tasks/dev.jsonl"
OUT = REPO / "runs/baseline-r2-c1/quality-summary.json"  # default (r2)

DEFAULT_THRESHOLD = 0.35  # configurable match threshold (Jaccard)

_STOP = frozenset({
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "by", "with", "from", "at", "as", "on",
    "it", "this", "that", "its", "which", "what", "how", "why", "not",
    "does", "do", "according", "records", "record", "metadata", "per",
    "refers", "referred", "related", "such", "than", "then", "into",
    "their", "there", "these", "those", "has", "have", "had", "will",
})


def normalize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in _STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def greedy_match(sys_claims: list[str], gold_claims: list[dict],
                 threshold: float) -> list[tuple[int, int, float]]:
    """Greedy best matching: sys_claim i ↔ gold_claim j (one-to-one).

    Deterministic: iterate candidate pairs sorted by (-score, i, j)."""
    sys_norm = [normalize(c) for c in sys_claims]
    gold_norm = [normalize(g["claim"]) for g in gold_claims]
    pairs = []
    for i, sn in enumerate(sys_norm):
        for j, gn in enumerate(gold_norm):
            pairs.append((-jaccard(sn, gn), i, j))
    pairs.sort()
    matched: list[tuple[int, int, float]] = []
    used_sys: set[int] = set()
    used_gold: set[int] = set()
    for neg, i, j in pairs:
        if i in used_sys or j in used_gold:
            continue
        score = -neg
        if score < threshold:
            continue
        matched.append((i, j, score))
        used_sys.add(i)
        used_gold.add(j)
    return matched


def eval_task(task: dict, report: dict, threshold: float) -> dict:
    # claims live under result.report.claims (report_min envelope shape);
    # C2: if augment.model_claims present, evaluate THOSE as system claims
    claims_block = (report.get("report") or {}).get("claims", [])
    augment = report.get("augment") or {}
    if augment.get("status") == "augmented" and augment.get("model_claims"):
        sys_claims = [c["claim"] for c in augment["model_claims"]]
        sys_sources = [c.get("source_ids", []) for c in augment["model_claims"]]
    else:
        sys_claims = [c["text"] for c in claims_block]
        sys_sources = [c.get("citations", []) for c in claims_block]
    gold_claims = task["weighted_gold_claims"]
    matched = greedy_match(sys_claims, gold_claims, threshold)

    # weight-aware recall: matched gold weight / total gold weight
    total_gold_w = sum(g["weight"] for g in gold_claims)
    matched_gold_w = sum(gold_claims[j]["weight"] for _, j, _ in matched)
    recall = matched_gold_w / total_gold_w if total_gold_w else 0.0

    # weight-aware precision: matched sys claims get gold weight of partner
    matched_sys_w = sum(gold_claims[j]["weight"] for _, j, _ in matched)
    total_sys = len(sys_claims)
    precision = matched_sys_w / total_sys if total_sys else 0.0

    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    # citation coverage: model claims cite source_ids; template claims cite
    cit_claims = [c for c in claims_block if c.get("citations")]
    if augment.get("status") == "augmented" and augment.get("model_claims"):
        cit_claims = [c for c in augment["model_claims"] if c.get("source_ids")]
    cit_cov = len(cit_claims) / total_sys if total_sys else 0.0

    return {
        "case_id": task["case_id"],
        "sys_claims": total_sys,
        "gold_claims": len(gold_claims),
        "matched": len(matched),
        "threshold": threshold,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "citation_coverage": round(cit_cov, 4),
        "matched_pairs": [{"sys": sys_claims[i][:80],
                           "gold": gold_claims[j]["claim"][:80],
                           "score": round(s, 4)}
                          for i, j, s in matched],
    }


def main(argv: list[str] | None = None) -> int:
    threshold = DEFAULT_THRESHOLD
    nonlocal_runs = "baseline-r2-c1"
    nonlocal_out = None
    nonlocal_gold = None
    nonlocal_reports = None
    if argv is None:
        argv = sys.argv[1:] if len(sys.argv) > 1 else []
    if argv:
        import argparse
        ap = argparse.ArgumentParser(prog="eval_quality.py")
        ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
        ap.add_argument("--runs", type=str, default="baseline-r2-c1")
        ap.add_argument("--out", type=str, default=None)
        ap.add_argument("--gold-file", type=str, default=None)
        ap.add_argument("--reports-dir", type=str, default=None)
        args = ap.parse_args(argv)
        threshold = args.threshold
        nonlocal_runs = args.runs
        nonlocal_out = args.out
        nonlocal_gold = args.gold_file
        nonlocal_reports = args.reports_dir

    global RUNS, OUT
    RUNS = REPO / f"runs/{nonlocal_runs}/tasks"
    if nonlocal_out:
        OUT = REPO / nonlocal_out
    gold_path = REPO / "dataset/v1/tasks/pilot-gold.jsonl" \
        if nonlocal_gold else DEV
    reports_root = REPO / nonlocal_reports if nonlocal_reports else RUNS
    dev = {json.loads(l)["case_id"]: json.loads(l)
           for l in gold_path.read_text(encoding="utf-8").splitlines()}
    per_task = []
    for case_id in sorted(dev):
        art = reports_root / case_id / "artifacts/report.json"
        if not art.exists():
            per_task.append({"case_id": case_id, "error": "missing report"})
            continue
        report = json.loads(art.read_text(encoding="utf-8"))
        if report.get("error") is not None or not report.get("result"):
            per_task.append({"case_id": case_id, "error": "failed run"})
            continue
        per_task.append(eval_task(dev[case_id], report["result"], threshold))

    ok = [t for t in per_task if "error" not in t]
    n = len(ok)
    agg = {
        "tasks_evaluated": n,
        "tasks_total": len(dev),
        "mean_precision": round(sum(t["precision"] for t in ok) / n, 4) if n else 0.0,
        "mean_recall": round(sum(t["recall"] for t in ok) / n, 4) if n else 0.0,
        "mean_f1": round(sum(t["f1"] for t in ok) / n, 4) if n else 0.0,
        "mean_citation_coverage": round(
            sum(t["citation_coverage"] for t in ok) / n, 4) if n else 0.0,
    }
    summary = {
        "run_id": "baseline-r2-c1",
        "eval": "PRE quality numbers (not evaluation conclusions)",
        "gold_source": "dev.jsonl single-annotator engineering-level gold",
        "threshold": threshold,
        "aggregate": agg,
        "per_task": per_task,
        "generated_at_utc": __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
    OUT.write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False,
                              indent=2) + "\n", encoding="utf-8")
    print(f"evaluated {n}/{len(dev)} tasks | "
          f"P={agg['mean_precision']} R={agg['mean_recall']} "
          f"F1={agg['mean_f1']} cit={agg['mean_citation_coverage']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
