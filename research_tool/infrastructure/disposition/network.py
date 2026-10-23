"""Reviewed source/claim mappings, not an unsourced clinical or causal graph."""

from __future__ import annotations

import copy

from .records import digest, require, validate_reference
from .state import validate_scope

EDGE_KINDS = {
    "requires_context",
    "context_of",
    "conflict_candidate",
    "secondary_citation_of",
    "possible_overlap_with",
    "reports_on",
}
PURPOSE_RULES = {
    "study_metadata": "identity_only",
    "descriptive": "scope_required",
    "cross_study": "independence_and_design_required",
}
PURPOSE_CHECKS = {
    "study_metadata": {"original_identity"},
    "descriptive": {
        "population",
        "unit",
        "denominator",
        "intervention",
        "time",
        "missingness",
        "repeated_events",
    },
    "cross_study": {"independence", "comparability", "bias", "precision"},
}


def validate_network(network, policy, roots):
    require(
        isinstance(network, dict)
        and {"schema", "evidence_refs", "assignments", "studies", "edges"} <= set(network)
        and set(network)
        <= {"schema", "evidence_refs", "assignments", "studies", "edges", "assessments"},
        "invalid network map fields",
    )
    require(network["schema"] == "network-evidence-map/v1", "unknown network map schema")
    require(
        isinstance(policy, dict)
        and set(policy) == {"schema", "policy_id", "version", "purposes"}
        and policy["schema"] == "assessment-policy/v1"
        and isinstance(policy["policy_id"], str)
        and policy["policy_id"].strip()
        and type(policy["version"]) is int
        and policy["version"] >= 1
        and policy["purposes"] == PURPOSE_RULES,
        "invalid purpose-specific assessment policy",
    )
    require(
        isinstance(network["evidence_refs"], list) and network["evidence_refs"],
        "network requires committed exact evidence",
    )
    facts, cache = {}, {}
    for ref in network["evidence_refs"]:
        require("claim_id" in ref, "network evidence must reference exact facts")
        fact = validate_reference(roots, ref, cache=cache)
        cid = ref["claim_id"]
        require(cid not in facts or facts[cid] == fact, "ambiguous cross-run claim identity")
        facts[cid] = fact
    assignments = set()
    for item in network["assignments"]:
        require(
            set(item) == {"claim_id", "concept_id", "role", "scope"}, "invalid mapping assignment"
        )
        require(
            item["claim_id"] in facts
            and isinstance(item["concept_id"], str)
            and item["concept_id"].strip(),
            "unknown mapped claim or concept",
        )
        validate_scope(item["scope"])
        require(item["scope"]["purpose"] in PURPOSE_RULES, "unsupported assessment purpose")
        require(
            item["role"]
            in {
                "primary_result",
                "method",
                "metadata",
                "secondary_citation",
                "mechanism",
                "case",
                "recommendation",
                "limitation",
            },
            "unknown evidence role",
        )
        key = (item["claim_id"], item["concept_id"], digest(item["scope"]))
        require(key not in assignments, "duplicate concept membership")
        assignments.add(key)
    sources = {c["source_id"] for f in facts.values() for c in f["citations"]}
    studies = set()
    for item in network["studies"]:
        require(
            set(item) == {"source_id", "study_id", "cohort_id", "evidence_claim_ids"},
            "invalid source/study record",
        )
        require(
            item["source_id"] in sources and item["source_id"] not in studies,
            "unknown or duplicate study source",
        )
        require(
            all(isinstance(item[k], str) and item[k].strip() for k in ["study_id", "cohort_id"]),
            "study/cohort identity is empty",
        )
        require(
            item["evidence_claim_ids"] and set(item["evidence_claim_ids"]) <= set(facts),
            "study mapping lacks exact original support",
        )
        require(
            all(
                any(c["source_id"] == item["source_id"] for c in facts[cid]["citations"])
                for cid in item["evidence_claim_ids"]
            ),
            "study support belongs to another source",
        )
        studies.add(item["source_id"])
    for edge in network["edges"]:
        require(
            set(edge) == {"kind", "from_claim_id", "to_claim_id", "evidence_claim_ids", "scope"},
            "unverified relation fields are forbidden",
        )
        require(
            edge["kind"] in EDGE_KINDS, "causal/unrecognized relation cannot enter reviewed network"
        )
        require(
            edge["from_claim_id"] in facts and edge["to_claim_id"] in facts,
            "edge endpoints lack exact claim identity",
        )
        require(edge["from_claim_id"] != edge["to_claim_id"], "self relation is invalid")
        require(
            edge["evidence_claim_ids"] and set(edge["evidence_claim_ids"]) <= set(facts),
            "relation requires exact supporting claims",
        )
        validate_scope(edge["scope"])
        if edge["kind"] in {"context_of", "requires_context"}:
            left, right = facts[edge["from_claim_id"]], facts[edge["to_claim_id"]]
            require(
                any(
                    a["source_id"] == b["source_id"] and a["content_sha256"] == b["content_sha256"]
                    for a in left["citations"]
                    for b in right["citations"]
                ),
                "context relationship cannot cross source versions",
            )
            if edge["kind"] == "context_of":
                require(
                    any(
                        a["source_id"] == b["source_id"]
                        and a["content_sha256"] == b["content_sha256"]
                        and a["start_offset"] <= b["start_offset"]
                        and a["end_offset"] >= b["end_offset"]
                        for a in left["citations"]
                        for b in right["citations"]
                    ),
                    "context_of requires exact span containment",
                )
    _validate_assessments(network, facts)
    return copy.deepcopy(network)


def _validate_assessments(network, facts):
    seen_assessments = set()
    for assessment in network.get("assessments", []):
        require(
            set(assessment) == {"concept_id", "scope_sha256", "checks"}, "invalid assessment fields"
        )
        key = (assessment["concept_id"], assessment["scope_sha256"])
        members = [
            a for a in network["assignments"] if (a["concept_id"], digest(a["scope"])) == key
        ]
        require(members and key not in seen_assessments, "unknown or duplicate assessment group")
        seen_assessments.add(key)
        requirements = PURPOSE_CHECKS[members[0]["scope"]["purpose"]]
        checked = set()
        for check in assessment["checks"]:
            require(
                set(check) == {"requirement", "status", "evidence_claim_ids", "reason"},
                "invalid purpose review check",
            )
            require(
                check["requirement"] in requirements and check["requirement"] not in checked,
                "unknown or duplicate purpose requirement",
            )
            checked.add(check["requirement"])
            require(check["status"] in {"met", "unmet", "unknown"}, "unknown assessment status")
            require(
                isinstance(check["reason"], str) and check["reason"].strip(),
                "missing check rationale",
            )
            require(
                set(check["evidence_claim_ids"]) <= set(facts), "assessment cites unknown evidence"
            )
            require(
                check["status"] != "met" or check["evidence_claim_ids"],
                "met requirement needs exact supporting evidence",
            )


def assess_network(network, policy):
    groups = {}
    source_studies = {item["source_id"]: item for item in network["studies"]}
    for item in network["assignments"]:
        key = digest([item["concept_id"], item["scope"]])
        group = groups.setdefault(
            key,
            {
                "concept_id": item["concept_id"],
                "scope": item["scope"],
                "claim_ids": [],
                "rule": policy["purposes"][item["scope"]["purpose"]],
                "scientific_sufficiency": "not_assessed",
            },
        )
        group["claim_ids"].append(item["claim_id"])
    for group in groups.values():
        group["claim_ids"] = sorted(set(group["claim_ids"]))
        group["known_cohort_groups"] = sorted(
            {
                item["cohort_id"]
                for item in source_studies.values()
                if set(item["evidence_claim_ids"]).intersection(group["claim_ids"])
            }
        )
        group["cohort_groups_are_independence_proof"] = False
        group["independent_cohort_count"] = None
        assessments = [
            a
            for a in network.get("assessments", [])
            if a["concept_id"] == group["concept_id"]
            and a["scope_sha256"] == digest(group["scope"])
        ]
        checks = {c["requirement"]: c for a in assessments for c in a["checks"]}
        required = PURPOSE_CHECKS[group["scope"]["purpose"]]
        group["unmet_requirements"] = sorted(
            k for k in required if checks.get(k, {}).get("status") != "met"
        )
        group["checks"] = [checks[k] for k in sorted(checks)]
        group["assessment_status"] = (
            "needs_review" if group["unmet_requirements"] else "requirements_met"
        )
        group["scientific_sufficiency"] = "not_inferred" if assessments else "not_assessed"
        group["required_review"] = (
            "original_identity"
            if group["rule"] == "identity_only"
            else "unit_denominator_intervention_and_time"
            if group["rule"] == "scope_required"
            else "cohort_overlap_comparability_bias_precision"
        )
    return {
        "schema": "network-assessment/v1",
        "network_sha256": digest(network),
        "policy_sha256": digest(policy),
        "groups": [groups[k] for k in sorted(groups)],
        "automatic_issue_closures": 0,
        "new_scientific_evidence_added": 0,
        "source_count_is_not_independence": True,
    }
