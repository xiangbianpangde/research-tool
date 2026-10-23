"""Pinned disposition consumption: a reviewed view never overwrites the raw gate."""

from __future__ import annotations

from .network import assess_network
from .records import digest, require
from .registry import Registry


def evaluate_registry(registry_root, expected_head, inspection, graph, *, known_limitations=()):
    """Apply only exact original observations, preserving unmatched/current scientific gaps.

    The original records stay visible. Every imported required limitation and every
    unresolved child remains in the reviewed required count even when absent from
    a later model's output. Matching only a model finding ID is never sufficient.
    """
    registry = Registry(registry_root)
    state = registry.snapshot(expected_head=expected_head, require_fresh=True)
    facts = {f["claim_id"]: f for f in graph["facts"]}
    current_source_versions = {s["source_id"]: s["content_sha256"] for s in graph["sources"]}
    # Every closure that removes a required obligation must still have its
    # evidence in the current graph, even if the model omits its original finding.
    evidence_cache = {}
    for issue in state["issues"].values():
        if issue["effective_lifecycle"] == "closed" and issue["resolution"] in {
            "corrected",
            "resolved",
        }:
            _validate_current_decision(state, issue, facts, current_source_versions, evidence_cache)
    matches, unresolved, corrected = [], [], []
    represented_by_active_issue = set()
    by_record = {}
    for observation in state["observations"].values():
        if observation["kind"] == "finding":
            by_record.setdefault(digest(observation["original"]), []).append(observation)
    for finding in inspection["findings"]:
        choices = by_record.get(digest(finding), [])
        if not choices:
            unresolved.append(finding)
            continue
        # No inference from a name or lexical similarity to an old observation.
        candidates = [
            x
            for x in state["issues"].values()
            if any(o["occurrence_id"] in x["origin_ids"] for o in choices)
        ]
        candidates = _canonical_leaves(state, candidates)
        active = [x for x in candidates if x["effective_lifecycle"] != "closed"]
        decisions = [
            x
            for x in candidates
            if x.get("resolution") in {"corrected", "resolved"} and not x["needs_revalidation"]
        ]
        if active or not candidates or len(decisions) != len(candidates):
            unresolved.append(finding)
            if any(x["required"] for x in active):
                represented_by_active_issue.add(digest(finding))
        else:
            corrected.append(finding["finding_id"])
        matches.extend(o["occurrence_id"] for o in choices)
    active_required = [
        x
        for x in state["issues"].values()
        if x["required"] and x["effective_lifecycle"] != "closed"
    ]
    # All issue types, including limitations and decomposed children, remain blocking
    # unless reviewed explicitly. Unmatched raw findings are an additional floor.
    unmatched = [f for f in unresolved if digest(f) not in represented_by_active_issue]
    imported_limits = {
        digest(o["original"]) for o in state["observations"].values() if o["kind"] == "limitation"
    }
    unmatched_limits = [text for text in known_limitations if digest(text) not in imported_limits]
    total = len(active_required) + len(unmatched) + len(unmatched_limits)
    high = sum(
        any(
            isinstance(state["observations"][oid]["original"], dict)
            and state["observations"][oid]["original"].get("priority") == "high"
            for oid in issue["origin_ids"]
        )
        for issue in active_required
    )
    high += sum(f["priority"] == "high" for f in unmatched)
    view = {
        "schema": "issue-evaluation/v1",
        "issue_head_sha256": expected_head,
        "registry_id": state["registry_id"],
        "raw_findings": inspection["findings"],
        "reviewed_unresolved_findings": unresolved,
        "corrected_original_ids": corrected,
        "matched_occurrence_ids": sorted(set(matches)),
        "counts": state["counts"],
        "required_unresolved_total": total,
        "required_unresolved_high": high,
        "active_required_issues": active_required,
        "constraints": list(state["constraints"].values()),
        "unmatched_current_limitations": unmatched_limits,
        "history_links": list(state.get("history_links", {}).values()),
        "decision_snapshot": registry.export(expected_head=expected_head),
        "network_sha256": digest(state["network"]),
        "policy_sha256": digest(state["policy"]),
        "network_assessment": assess_network(state["network"], state["policy"])
        if state["network"] is not None
        else None,
        "new_scientific_evidence_added": state["counts"]["new_scientific_evidence_added"],
        "hard_evidence_checks_waivable": False,
    }
    return view


def _canonical_leaves(state, candidates):
    leaves, pending, seen = {}, list(candidates), set()
    while pending:
        issue = pending.pop()
        if issue["issue_id"] in seen:
            continue
        seen.add(issue["issue_id"])
        if issue["effective_lifecycle"] == "closed" and issue["resolution"] in {
            "duplicate",
            "superseded",
        }:
            pending.extend(state["issues"][link["target"]] for link in issue["links"])
        elif issue["effective_lifecycle"] == "closed" and issue["resolution"] == "decomposed":
            pending.extend(state["issues"][cid] for cid in issue["children"])
        else:
            leaves[issue["issue_id"]] = issue
    return list(leaves.values())


def _validate_current_decision(state, issue, facts, source_versions, cache):
    from .records import validate_reference

    proposal = state["proposals"][issue["last_decision"]["proposal_id"]]
    for ref in proposal["payload"].get("evidence_refs", []):
        if "claim_id" not in ref:
            continue
        require(ref["claim_id"] in facts, "reviewed evidence is not in current graph")
        original = validate_reference(state["roots"], ref, cache=cache)
        current = facts[ref["claim_id"]]
        require(
            original["text"] == current["text"] and original["citations"] == current["citations"],
            "current graph differs from reviewed scope/source spans",
        )
        require(
            all(
                source_versions.get(c["source_id"]) == c["content_sha256"]
                for c in original["citations"]
            ),
            "reviewed current source version changed",
        )


def reviewed_gate(raw_decision, evaluation, *, max_high, max_total, evidence_policy, final_round):
    if raw_decision["verdict"] in {"STOP_ERROR", "STOP_BUDGET", "STOP_CANCELLED"}:
        return {
            **raw_decision,
            "raw_verdict": raw_decision["verdict"],
            "issue_contract": "issue-evaluation/v1",
            "issue_head_sha256": evaluation["issue_head_sha256"],
        }
    strict = (
        "CONTINUE"
        if evaluation["required_unresolved_high"] > max_high
        or evaluation["required_unresolved_total"] > max_total
        else "STOP_SUCCESS"
    )
    return {
        **raw_decision,
        "raw_verdict": raw_decision["verdict"],
        "raw_strict_verdict": raw_decision.get("strict_verdict", raw_decision["verdict"]),
        "verdict": "STOP_SUCCESS" if evidence_policy == "layered" and final_round else strict,
        "strict_verdict": strict,
        "reviewed_verdict": strict,
        "issue_contract": "issue-evaluation/v1",
        "issue_head_sha256": evaluation["issue_head_sha256"],
        "reviewed_high": evaluation["required_unresolved_high"],
        "reviewed_total": evaluation["required_unresolved_total"],
        "raw_findings_preserved": True,
    }
