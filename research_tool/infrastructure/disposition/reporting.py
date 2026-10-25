"""Exact reviewed interpretation projection for report-time consumption, never new facts."""

from __future__ import annotations

from .records import digest, require, validate_reference
from .network import assess_network


def reviewed_dependencies(state, graph):
    """Bring approved literal dependencies through normal merge, never change source bodies."""
    references = list((state["network"] or {}).get("evidence_refs", []))
    for issue in state["issues"].values():
        if issue["effective_lifecycle"] == "closed" and issue["resolution"] in {
            "corrected",
            "resolved",
        }:
            proposal = state["proposals"][issue["last_decision"]["proposal_id"]]
            references.extend(proposal["payload"].get("evidence_refs", []))
    existing = {f["claim_id"]: f for f in graph["facts"]}
    sources = {s["source_id"]: s for s in graph["sources"]}
    added, cache = {}, {}
    for ref in references:
        if "claim_id" not in ref:
            continue
        fact = validate_reference(state["roots"], ref, cache=cache)
        for citation in fact["citations"]:
            source = sources.get(citation["source_id"])
            require(
                source is not None
                and source["content_sha256"] == citation["content_sha256"]
                and source["locator"] == citation["locator"],
                "reviewed dependency source version is not in current corpus",
            )
        if fact["claim_id"] in existing:
            require(
                existing[fact["claim_id"]]["text"] == fact["text"]
                and existing[fact["claim_id"]]["citations"] == fact["citations"],
                "current claim conflicts with reviewed dependency",
            )
        else:
            added[fact["claim_id"]] = fact
    return {
        "facts": [added[k] for k in sorted(added)],
        "sources": graph["sources"],
        "new_scientific_evidence_added": 0,
        "dependency_scope": "exact_committed_literals_in_same_current_source_bodies",
    }


def report_semantics(state, graph):
    network = state["network"]
    if network is None:
        return None
    facts = {f["claim_id"]: f for f in graph["facts"]}
    versions = {s["source_id"]: s["content_sha256"] for s in graph["sources"]}
    cache = {}
    for ref in network["evidence_refs"]:
        original = validate_reference(state["roots"], ref, cache=cache)
        current = facts.get(ref["claim_id"])
        require(
            current is not None
            and current["text"] == original["text"]
            and current["citations"] == original["citations"],
            "reviewed semantic claim differs from current report graph",
        )
        require(
            all(versions.get(c["source_id"]) == c["content_sha256"] for c in original["citations"]),
            "reviewed semantic source version changed",
        )
    return {
        "schema": "reviewed-report-semantics/v1",
        "issue_head_sha256": state["head_sha256"],
        "registry_id": state["registry_id"],
        "network_sha256": digest(network),
        "policy_sha256": digest(state["policy"]),
        "assignments": network["assignments"],
        "edges": network["edges"],
        "studies": network["studies"],
        "assessment": assess_network(network, state["policy"]),
        "unmapped_claim_ids": sorted(set(facts) - {a["claim_id"] for a in network["assignments"]}),
        "interpretations_are_not_new_source_evidence": True,
    }


def source_semantics(packet, source_id, facts):
    if packet is None:
        return None
    ids = {
        cid for cid, f in facts.items() if any(c["source_id"] == source_id for c in f["citations"])
    }
    return {
        **{
            k: packet[k] for k in ["schema", "issue_head_sha256", "network_sha256", "policy_sha256"]
        },
        "assignments": [a for a in packet["assignments"] if a["claim_id"] in ids],
        "edges": [
            e for e in packet["edges"] if e["from_claim_id"] in ids or e["to_claim_id"] in ids
        ],
        "assessment_groups": [
            g for g in packet["assessment"]["groups"] if set(g["claim_ids"]) & ids
        ],
        "boundary": (
            "Agent-reviewed interpretation; no independent-cohort "
            "or scientific-sufficiency inference"
        ),
    }


def prompt_semantics(packet, to_short, facts):
    """Prompt-only ID projection; cross-source links retain explicit external bindings."""
    if packet is None:
        return None

    def transform(value, key=None):
        if isinstance(value, dict):
            return {k: transform(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(v, key) for v in value]
        if key in {"claim_id", "from_claim_id", "to_claim_id", "claim_ids", "evidence_claim_ids"}:
            if value in to_short:
                return to_short[value]
            require(value in facts, "semantic prompt references unknown claim")
            return {
                "external_claim_id": value,
                "text": facts[value]["text"],
                "scope": "outside_current_source_not_a_valid_local_supporting_id",
            }
        return value

    return transform(packet)


def render_source_semantics(packet, source_id, facts, indices):
    """Direct report-body disclosure, with exact original quotes and audited semantic scope."""
    selected = source_semantics(packet, source_id, facts)
    if not selected or not selected["assignments"]:
        return [], []

    def safe(value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("|", "\\|")
            .replace("\n", " ")
        )

    lines = [
        "### 经审核的证据用途与范围",
        "",
        "以下为固定版本的Agent审核解释，不是新增原始证据；未映射事实不因此获得审核保证。",
        "",
    ]
    claims = []
    # Navigation may map a literal to several reviewed issues. Display each
    # immutable literal once per source rather than making duplicate citations
    # look like independent evidence; retain every scope in the pinned packet.
    shown = set()
    for assignment in selected["assignments"]:
        if assignment["claim_id"] in shown:
            continue
        shown.add(assignment["claim_id"])
        fact = facts[assignment["claim_id"]]
        refs = " ".join(f"[{indices[c['source_id']]}]" for c in fact["citations"])
        aliases = [a for a in selected["assignments"] if a["claim_id"] == assignment["claim_id"]]
        lines.extend(
            ["**" + safe(assignment["concept_id"]) + "** · " + safe(assignment["role"]), ""]
        )
        if len(aliases) > 1:
            lines.extend(
                [
                    f"同一原句关联{len(aliases)}项审核用途，仅展示首项；其余scope在固定审计中，"
                    "不能计作多份独立证据。",
                    "",
                ]
            )
        labels = {
            "population": "人群",
            "challenge": "挑战/索引事件",
            "intervention_condition": "干预条件",
            "outcome": "结局",
            "time_window": "时间",
            "denominator_unit": "分母单位",
            "purpose": "用途",
            "target_requirement": "核验要求",
        }
        lines.extend(
            "- " + label + "：" + safe(assignment["scope"][key]) for key, label in labels.items()
        )
        for alternate in aliases[1:]:
            lines.extend(
                [
                    "- 另一已审用途："
                    + safe(alternate["concept_id"])
                    + " · "
                    + safe(alternate["role"]),
                    "  - 范围："
                    + "；".join(
                        label + "：" + safe(alternate["scope"][key])
                        for key, label in labels.items()
                    ),
                ]
            )
        group = next(
            g
            for g in packet["assessment"]["groups"]
            if g["concept_id"] == assignment["concept_id"]
            and digest(g["scope"]) == digest(assignment["scope"])
        )
        lines.extend(
            [
                "- 未满足/未知要求："
                + (", ".join(group["unmet_requirements"]) or "本用途已核对；不推出科学充分"),
                "- 独立队列数：未认定；同源上下文/转引/同库不算新增研究。",
                "",
                "**固定原文证据：**",
                "",
                fact["text"] + " " + refs,
                "",
            ]
        )
        claims.append(
            {"text": fact["text"], "claim_ids": [fact["claim_id"]], "citations": fact["citations"]}
        )
    return lines, claims
