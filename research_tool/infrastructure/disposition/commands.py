"""Bounded JSON command dispatcher. No model output is a write authorization."""

from __future__ import annotations

from pathlib import Path

from .records import require
from .registry import Registry


def execute(action, registry_root: Path, request=None):
    request = request or {}
    require(isinstance(request, dict), "issue request must be an object")
    if action == "create":
        require(set(request) == {"registry_id", "roots"}, "create requires registry_id and roots")
        registry = Registry.create(registry_root, **request)
        return {"registry_id": request["registry_id"], "head_sha256": registry.head()}
    registry = Registry(registry_root)
    if action == "view":
        require(not request, "view does not accept write fields")
        return registry.view()
    methods = {
        "snapshot": registry.snapshot,
        "import": registry.import_audit,
        "claim-reference": registry.claim_reference,
        "propose": registry.propose,
        "review": registry.review,
        "propose-network": registry.propose_network,
        "review-network": registry.review_network,
    }
    require(action in methods, "unsupported issue action")
    try:
        return methods[action](**request)
    except TypeError as exc:
        raise ValueError("invalid fields for issue command") from exc
