"""Closed ledger schemas: rehashing an unsupported record never makes it applicable."""

from __future__ import annotations

import re
from datetime import datetime

from .records import digest, occurrence_id, require


def fields(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected.split()), label)


def hex_value(value, size=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value)


def _validate_request(event):
    kind, request, data = event["type"], event["request"], event["payload"]
    if kind in {"initialize", "checkpoint"}:
        require(request == data, "event request/payload mismatch")
    elif kind == "import":
        fields(request, "root_id run_id audit_sha256", "invalid import request")
        for observation in data["observations"]:
            ref = observation["origin"]
            require(
                ref["root_id"] == request["root_id"]
                and ref["run_id"] == request["run_id"]
                and ref["artifact_sha256"] == request["audit_sha256"],
                "import request origin mismatch",
            )
    elif kind == "propose":
        fields(request, "issue_id operation revision payload", "invalid proposal request")
        proposal = data["proposal"]
        require(
            request
            == {
                "issue_id": proposal["issue_id"],
                "operation": proposal["operation"],
                "revision": proposal["issue_revision"],
                "payload": proposal["payload"],
            },
            "proposal request/payload mismatch",
        )
    elif kind == "network_propose":
        fields(request, "network policy", "invalid network request")
        require(
            request == {k: data["proposal"][k] for k in ["network", "policy"]},
            "network request/payload mismatch",
        )
    elif kind in {"review", "network_review"}:
        expected = "proposal_id proposal_sha256 approve reason authority"
        if kind == "network_review":
            expected += " reviewer_label"
        fields(request, expected, "invalid review request")
        require(all(data[k] == value for k, value in request.items()), "review request mismatch")


def validate_event(event):
    fields(
        event,
        "event_id type payload request idempotency_key request_sha256 "
        "actor_label actor_authority timestamp_utc receipt",
        "unknown event fields",
    )
    require(hex_value(event["event_id"], 32), "invalid event ID")
    require(event["actor_authority"] == "self_asserted_agent", "unsupported event authority")
    require(
        all(
            isinstance(event[k], str) and event[k].strip()
            for k in ["idempotency_key", "actor_label", "timestamp_utc"]
        ),
        "invalid event labels",
    )
    try:
        timestamp = datetime.fromisoformat(event["timestamp_utc"])
        require(
            timestamp.utcoffset() is not None and timestamp.utcoffset().total_seconds() == 0,
            "event timestamp must be UTC",
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid event timestamp") from exc
    require(
        event["request_sha256"]
        == digest(
            {"type": event["type"], "payload": event["request"], "actor": event["actor_label"]}
        ),
        "request digest mismatch",
    )
    kind, data = event["type"], event["payload"]
    expected = {
        "initialize": "registry_id roots",
        "import": "observations",
        "propose": "proposal",
        "review": (
            "proposal_id proposal_sha256 approve reason authority context_sha256 reviewer_label"
        ),
        "network_propose": "proposal",
        "network_review": "proposal_id proposal_sha256 approve reason reviewer_label authority",
        "checkpoint": "reason",
    }
    require(kind in expected, "unknown ledger event type")
    fields(data, expected[kind], "unknown event payload fields")
    receipt = {"event_id": event["event_id"], "event_type": kind, "payload_sha256": digest(data)}
    if kind in {"propose", "network_propose"}:
        p = data["proposal"]
        fields(
            p,
            "proposal_id issue_id issue_revision operation payload context_sha256"
            if kind == "propose"
            else "proposal_id network policy previous_network_sha256 previous_policy_sha256",
            "unknown proposal fields",
        )
        require(
            isinstance(p["proposal_id"], str)
            and re.fullmatch(r"(?:PROP|NET)-[0-9a-f]{32}", p["proposal_id"]),
            "invalid proposal identity",
        )
        receipt.update(proposal_id=p["proposal_id"], proposal_sha256=digest(p))
    if kind in {"review", "network_review"}:
        require(
            type(data["approve"]) is bool and data["authority"] == "agent_source_review",
            "invalid review decision/authority",
        )
        require(
            isinstance(data["reason"], str) and data["reason"].strip(), "empty review rationale"
        )
        require(data["reviewer_label"] == event["actor_label"], "review actor mismatch")
    require(event["receipt"] == receipt, "event receipt mismatch")
    _validate_request(event)
    if kind == "import":
        require(isinstance(data["observations"], list), "invalid observations")
        for observation in data["observations"]:
            fields(
                observation,
                "occurrence_id kind origin original finding_id required execution_verdict",
                "unknown observation fields",
            )
            require(
                observation["occurrence_id"] == occurrence_id(observation["origin"]),
                "observation identity mismatch",
            )
            require(
                digest(observation["original"]) == observation["origin"]["value_sha256"],
                "observation original differs",
            )
            require(
                observation["kind"] in {"finding", "limitation"}
                and observation["required"] is True,
                "invalid imported observation type/requirement",
            )
            name = (
                "unresolved_findings" if observation["kind"] == "finding" else "known_limitations"
            )
            require(
                re.fullmatch(
                    "/" + name + r"/(?:0|[1-9][0-9]*)", observation["origin"]["json_pointer"]
                ),
                "observation pointer mismatch",
            )
