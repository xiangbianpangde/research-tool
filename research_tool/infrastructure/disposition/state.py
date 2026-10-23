"""Pure, conservative state transitions. Closing work never certifies scientific truth."""

from __future__ import annotations

import copy

from .records import digest, require

ASSESSMENTS = {
    "unassessed",
    "partially_answered",
    "awaiting_material",
    "awaiting_data",
    "not_identifiable_under_scope",
}
RESOLUTIONS = {
    "resolved",
    "corrected",
    "duplicate",
    "decomposed",
    "out_of_scope",
    "superseded",
    "reclassified_constraint",
}
SCOPE_FIELDS = {
    "purpose",
    "population",
    "challenge",
    "intervention_condition",
    "outcome",
    "time_window",
    "denominator_unit",
    "target_requirement",
}


def scope_for(question):
    return {
        key: (
            "original_observation_review"
            if key == "purpose"
            else question
            if key == "target_requirement"
            else "unknown"
        )
        for key in SCOPE_FIELDS
    }


def validate_scope(scope):
    require(
        isinstance(scope, dict)
        and set(scope) == SCOPE_FIELDS
        and all(isinstance(v, str) and v.strip() for v in scope.values()),
        "invalid atomic scope",
    )


def issue_for(registry_id, observation):
    oid = observation["occurrence_id"]
    question = (
        observation["original"].get("description", "")
        if isinstance(observation["original"], dict)
        else observation["original"]
    )
    return {
        "issue_id": "ISS-" + digest([registry_id, oid])[:32],
        "origin_ids": [oid],
        "scope": scope_for(question),
        "required": observation["required"],
        "kind": "unclassified",
        "revision": 1,
        "lifecycle": "open",
        "assessment": "unassessed",
        "resolution": None,
        "scientific_answered": False,
        "new_scientific_evidence_added": False,
        "children": [],
        "links": [],
        "pending_proposal_id": None,
    }


def validate_disposition(state, issue, operation, payload):  # noqa: PLR0915 - transition boundary
    require(
        operation in RESOLUTIONS | {"assess", "reopen", "classify", "link_history"},
        "unknown disposition operation",
    )
    require(isinstance(payload, dict), "invalid disposition payload")
    require(
        isinstance(payload.get("rationale"), str) and payload["rationale"].strip(),
        "a decision needs an auditable rationale",
    )
    common = {"rationale", "evidence_refs", "scientific_answered", "new_scientific_evidence_added"}
    extras = {
        "assess": {"assessment"},
        "decomposed": {"parts"},
        "duplicate": {"target_issue_id"},
        "superseded": {"target_issue_id"},
        "resolved": {"acceptance_met", "acceptance_evidence"},
        "corrected": set(),
        "out_of_scope": set(),
        "reclassified_constraint": set(),
        "reopen": set(),
        "classify": {"kind", "scope"},
        "link_history": {"relation", "target_ref"},
    }
    require(
        isinstance(payload, dict) and set(payload) <= common | extras[operation],
        "unknown disposition payload fields",
    )
    refs = payload.get("evidence_refs", [])
    require(isinstance(refs, list), "invalid evidence references")
    answered = payload.get("scientific_answered", False)
    novel = payload.get("new_scientific_evidence_added", False)
    require(type(answered) is bool and type(novel) is bool, "decision flags must be booleans")
    require(
        not answered or (operation == "resolved" and issue["kind"] == "scientific_gap"),
        "only a reviewed scientific resolution can answer a scientific question",
    )
    require(
        not novel or answered, "new scientific evidence is not inferred from administrative closure"
    )
    if operation == "link_history":
        require(
            payload.get("relation") in {"related_to", "superseded_for_use"},
            "invalid history relation",
        )
        require(payload.get("target_ref") in refs, "history target needs exact committed reference")
    if operation == "assess":
        require(payload.get("assessment") in ASSESSMENTS, "invalid issue assessment")
    if operation == "classify":
        require(
            issue["kind"] == "unclassified" or issue.get("classification_reopened", False),
            "already classified issues require explicit reopen",
        )
        require(refs, "classification requires exact source support")
        require(
            payload.get("kind")
            in {
                "report_wording",
                "representation",
                "scientific_gap",
                "source_conflict",
                "material_task",
                "persistent_constraint",
                "history",
            },
            "unknown issue kind",
        )
        if issue["kind"] != "unclassified":
            require(
                payload["kind"] == issue["kind"],
                "reclassification cannot downgrade an existing required issue kind",
            )
        validate_scope(payload.get("scope"))
        require(
            payload["scope"]["target_requirement"] == issue["scope"]["target_requirement"],
            "classification cannot rewrite the original requirement",
        )
        require(
            all(
                payload["scope"][key] == issue["scope"][key] or issue["scope"][key] == "unknown"
                for key in SCOPE_FIELDS
                if key != "purpose"
            ),
            "classification cannot replace known clinical scope",
        )
        require(
            payload["scope"]["purpose"] == payload["kind"], "classification kind/purpose mismatch"
        )
    if "acceptance_evidence" in payload:
        acceptance = payload["acceptance_evidence"]
        require(
            isinstance(acceptance, list)
            and all(
                isinstance(row, dict)
                and set(row) == {"criterion", "evidence_ref"}
                and row["criterion"] == issue["scope"]["target_requirement"]
                and row["evidence_ref"] in refs
                for row in acceptance
            ),
            "acceptance references must bind the atomic requirement to validated evidence",
        )
    if operation in {"resolved", "corrected"}:
        require(
            refs and any("claim_id" in r for r in refs), "closure needs existing committed claims"
        )
        require(
            issue["assessment"]
            not in {"awaiting_data", "awaiting_material", "not_identifiable_under_scope"},
            "waiting or unidentifiable questions cannot be resolved",
        )
        require(
            issue["kind"] in {"report_wording", "representation", "scientific_gap"}
            and issue["scope"]["purpose"] == issue["kind"],
            "unclassified scope cannot close",
        )
        if issue["kind"] == "scientific_gap":
            require(
                operation == "resolved" and answered, "scientific unknown cannot be corrected away"
            )
            require(
                all(value != "unknown" for value in issue["scope"].values()),
                "scientific resolution requires all clinical scope fields",
            )
            acceptance = payload.get("acceptance_evidence", [])
            require(
                len(acceptance) == 1
                and set(acceptance[0]) == {"criterion", "evidence_ref"}
                and acceptance[0]["criterion"] == issue["scope"]["target_requirement"]
                and acceptance[0]["evidence_ref"] in refs,
                "scientific acceptance must bind its exact criterion to committed evidence",
            )
        else:
            require(
                not answered and not novel, "wording/network correction adds no scientific answer"
            )
        if operation == "resolved":
            require(payload.get("acceptance_met") is True, "resolution must meet atomic acceptance")
    if operation == "decomposed":
        require(
            not issue["children"], "reopened parent retains existing children; do not split twice"
        )
        parts = payload.get("parts", [])
        require(len(parts) >= 2, "decomposition needs all atomic parts")
        for part in parts:
            require(set(part) == {"scope", "coverage_text"}, "invalid atomic part")
            validate_scope(part["scope"])
            require(
                all(
                    part["scope"][key] == issue["scope"][key]
                    for key in SCOPE_FIELDS
                    if key != "target_requirement"
                ),
                "decomposition cannot change clinical scope",
            )
            require(
                part["scope"]["target_requirement"] == part["coverage_text"],
                "child requirement must be its exact covered original clause",
            )
            require(part["coverage_text"].strip(), "part has no original clause mapping")
        original = issue["scope"]["target_requirement"]
        covered = "".join(p["coverage_text"] for p in parts)
        require(
            covered == original, "decomposition must preserve every original character in order"
        )
    if operation in {"duplicate", "superseded"}:
        require(
            refs
            and issue["kind"] != "unclassified"
            and all(value != "unknown" for value in issue["scope"].values()),
            "linked consolidation needs complete classified scope and exact evidence",
        )
        target = payload.get("target_issue_id")
        require(target in state["issues"] and target != issue["issue_id"], "invalid linked issue")
        candidate = state["issues"][target]
        require(
            candidate["lifecycle"] not in {"closed", "pending_review"},
            "linked issue must retain an active canonical target",
        )
        require(
            candidate["kind"] == issue["kind"] and candidate["scope"] == issue["scope"],
            "linked issue has different atomic kind/scope",
        )
        require(
            not issue["required"] or candidate["required"], "canonical target loses requiredness"
        )

        def high(item):
            return any(
                isinstance(state["observations"][oid]["original"], dict)
                and state["observations"][oid]["original"].get("priority") == "high"
                for oid in item.get("origin_ids", [])
            )

        require(not high(issue) or high(candidate), "canonical target loses high priority")
        # Previously linked targets may not subsequently link away through the old node.
        pending = [target]
        seen = set()
        while pending:
            current = pending.pop()
            require(current != issue["issue_id"], "issue relation cycle")
            if current not in seen:
                seen.add(current)
                pending.extend(state["issues"][current]["children"])
                pending.extend(x["target"] for x in state["issues"][current]["links"])
    if operation in {"out_of_scope", "reclassified_constraint"}:
        require(
            (
                not issue["required"]
                or (
                    operation == "reclassified_constraint"
                    and issue["kind"] in {"persistent_constraint", "history"}
                )
            ),
            "cannot remove a required issue by scope/constraint reclassification",
        )
        require(refs, "scope/constraint decision needs exact original evidence")
    if operation == "reopen":
        require(issue["lifecycle"] == "closed", "only closed issues can reopen")
    else:
        require(issue["lifecycle"] != "closed", "closed issues must be explicitly reopened first")
    # Inbound requirements must retain a terminal canonical issue, but that issue
    # may be genuinely corrected/resolved or decomposed into visible children.
    if operation in {"duplicate", "superseded", "out_of_scope", "reclassified_constraint"}:
        require(
            not any(
                link["target"] == issue["issue_id"]
                for item in state["issues"].values()
                if item["resolution"] in {"duplicate", "superseded"}
                for link in item["links"]
            ),
            "canonical issue has dependent unresolved links",
        )


def context_digest(state, issue):
    return digest(
        {
            "scope": issue["scope"],
            "origins": [state["observations"][oid]["origin"] for oid in issue["origin_ids"]],
            "network": digest(state["network"]),
            "policy": digest(state["policy"]),
        }
    )


def apply_event(state, event):  # noqa: PLR0915 - single deterministic replay contract
    kind, data = event["type"], event["payload"]
    if kind == "initialize":
        require(state is None, "duplicate registry initialization")
        return {
            "schema": "issue-state/v1",
            "registry_id": data["registry_id"],
            "roots": data["roots"],
            "observations": {},
            "issues": {},
            "constraints": {},
            "proposals": {},
            "network_proposals": {},
            "history": [],
            "network": None,
            "policy": None,
        }
    state = copy.deepcopy(state)
    if kind == "import":
        for observation in data["observations"]:
            oid = observation["occurrence_id"]
            if oid in state["observations"]:
                require(state["observations"][oid] == observation, "observation identity conflict")
                continue
            state["observations"][oid] = observation
            issue = issue_for(state["registry_id"], observation)
            require(issue["issue_id"] not in state["issues"], "issue identity collision")
            state["issues"][issue["issue_id"]] = issue
    elif kind == "propose":
        proposal = data["proposal"]
        require(proposal["proposal_id"] not in state["proposals"], "duplicate proposal identity")
        issue = state["issues"][proposal["issue_id"]]
        validate_disposition(state, issue, proposal["operation"], proposal["payload"])
        require(
            issue["revision"] == proposal["issue_revision"]
            and issue["pending_proposal_id"] is None,
            "issue proposal revision conflict",
        )
        issue["lifecycle_before_proposal"] = issue["lifecycle"]
        issue["lifecycle"] = "pending_review"
        issue["pending_proposal_id"] = proposal["proposal_id"]
        issue["revision"] += 1
        state["proposals"][proposal["proposal_id"]] = proposal
    elif kind == "review":
        proposal = state["proposals"][data["proposal_id"]]
        issue = state["issues"][proposal["issue_id"]]
        require(
            issue["pending_proposal_id"] == proposal["proposal_id"]
            and issue["revision"] == proposal["issue_revision"] + 1,
            "stale issue review",
        )
        require(data["proposal_sha256"] == digest(proposal), "review proposal hash mismatch")
        require(data["context_sha256"] == proposal["context_sha256"], "review context mismatch")
        issue["lifecycle"] = issue.pop("lifecycle_before_proposal")
        if data["approve"]:
            require(
                context_digest(state, issue) == proposal["context_sha256"],
                "review context is stale at replay boundary",
            )
            op, payload = proposal["operation"], proposal["payload"]
            validate_disposition(state, issue, op, payload)
            if op == "classify":
                issue["kind"] = payload["kind"]
                issue["scope"] = payload["scope"]
                issue["lifecycle"] = "working"
                issue["classification_reopened"] = False
            elif op == "link_history":
                hid = "HIS-" + digest(proposal)[:32]
                state.setdefault("history_links", {})[hid] = {
                    "history_link_id": hid,
                    "issue_id": issue["issue_id"],
                    "origin_ids": issue["origin_ids"],
                    "relation": payload["relation"],
                    "target_ref": payload["target_ref"],
                    "evidence_refs": payload["evidence_refs"],
                    "reviewed_proposal_sha256": digest(proposal),
                }
            elif op == "assess":
                issue["assessment"] = payload["assessment"]
                issue["lifecycle"] = "working"
            elif op == "reopen":
                issue.update(
                    lifecycle="working",
                    resolution=None,
                    scientific_answered=False,
                    new_scientific_evidence_added=False,
                    classification_reopened=True,
                    links=[],
                )
                for constraint in state["constraints"].values():
                    if constraint["issue_id"] == issue["issue_id"]:
                        constraint["applicability"] = "superseded"
            else:
                issue.update(
                    lifecycle="closed",
                    resolution=op,
                    scientific_answered=payload.get("scientific_answered", False),
                    new_scientific_evidence_added=payload.get(
                        "new_scientific_evidence_added", False
                    ),
                )
                if op == "decomposed":
                    for index, part in enumerate(payload["parts"]):
                        child = copy.deepcopy(issue)
                        for field in [
                            "last_decision",
                            "decision_scope_sha256",
                            "decision_network_sha256",
                            "decision_policy_sha256",
                        ]:
                            child.pop(field, None)
                        child_id = "ISS-" + digest([proposal["proposal_id"], index])[:32]
                        child.update(
                            issue_id=child_id,
                            scope=part["scope"],
                            revision=1,
                            lifecycle="open",
                            assessment="unassessed",
                            resolution=None,
                            children=[],
                            links=[],
                            pending_proposal_id=None,
                        )
                        state["issues"][child_id] = child
                        issue["children"].append(child_id)
                elif op in {"duplicate", "superseded"}:
                    issue["links"].append({"kind": op, "target": payload["target_issue_id"]})
                elif op == "reclassified_constraint":
                    cid = "CON-" + digest(proposal)[:32]
                    state["constraints"][cid] = {
                        "constraint_id": cid,
                        "issue_id": issue["issue_id"],
                        "scope": issue["scope"],
                        "origin_ids": issue["origin_ids"],
                        "evidence_refs": payload["evidence_refs"],
                        "applicability": "active",
                    }
            if op != "link_history":
                issue["last_decision"] = data
                # Only disposition reviews renew validity; metadata never does.
                issue["decision_scope_sha256"] = digest(issue["scope"])
                issue["decision_network_sha256"] = digest(state["network"])
                issue["decision_policy_sha256"] = digest(state["policy"])
        else:
            issue["lifecycle"] = "working" if issue["lifecycle"] != "closed" else "closed"
        issue["pending_proposal_id"] = None
        issue["revision"] += 1
        state["history"].append(data)
    elif kind == "checkpoint":
        require(
            set(data) == {"reason"} and isinstance(data["reason"], str) and data["reason"].strip(),
            "invalid transaction checkpoint",
        )
        state["history"].append({"kind": "checkpoint", "reason": data["reason"]})
    elif kind == "network_propose":
        proposal = data["proposal"]
        require(
            proposal["proposal_id"] not in state["network_proposals"], "duplicate network proposal"
        )
        require(
            proposal["previous_network_sha256"] == digest(state["network"])
            and proposal["previous_policy_sha256"] == digest(state["policy"]),
            "network proposal context changed",
        )
        state["network_proposals"][proposal["proposal_id"]] = proposal
    elif kind == "network_review":
        proposal = state["network_proposals"][data["proposal_id"]]
        require(
            not any(x.get("proposal_id") == data["proposal_id"] for x in state["history"]),
            "network proposal was already reviewed",
        )
        require(
            data["proposal_sha256"] == digest(proposal), "network review proposal hash mismatch"
        )
        require(
            not data["approve"]
            or (
                proposal["previous_network_sha256"] == digest(state["network"])
                and proposal["previous_policy_sha256"] == digest(state["policy"])
            ),
            "network review context is stale",
        )
        if data["approve"]:
            state["network"] = proposal["network"]
            state["policy"] = proposal["policy"]
        state["history"].append(data)
    else:
        require(False, "unknown disposition event")
    return state


def counts(state):
    issues = list(state["issues"].values())
    closed = [x for x in issues if x.get("effective_lifecycle", x["lifecycle"]) == "closed"]
    unresolved = [x for x in issues if x.get("effective_lifecycle", x["lifecycle"]) != "closed"]
    return {
        "original_observations": len(state["observations"]),
        "atomic_issues": len(issues),
        "required_unresolved": sum(x["required"] for x in unresolved),
        "open": len(unresolved),
        "closed": len(closed),
        "constraints": sum(c["applicability"] == "active" for c in state["constraints"].values()),
        "administratively_disposed": len(closed),
        "scientifically_answered": sum(x["scientific_answered"] for x in closed),
        "new_scientific_evidence_added": sum(x["new_scientific_evidence_added"] for x in closed),
    }
