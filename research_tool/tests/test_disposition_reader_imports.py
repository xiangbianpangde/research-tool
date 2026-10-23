"""Complete imported audit, exact origin shape and mixed terminal branches remain fail-closed."""

import sys
import copy
import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_reader_rejects_dropped_audit_observations_and_mixed_origin(corpus):
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evidence import verify_imports, verify_origins
    from researchctl.layouts.research_tool import LayoutError

    reg, _, _, graph = imported(corpus)
    e = evaluate_registry(reg.root, reg.head(), {"findings": []}, graph)
    snap = e["decision_snapshot"]
    _, _, events = reg._read()
    changed = copy.deepcopy(events)
    next(event for event in changed if event["type"] == "import")["payload"]["observations"] = []
    with pytest.raises(LayoutError, match="omitted"):
        verify_imports(changed, snap["evidence_artifacts"])
    state = copy.deepcopy(snap["state"])
    next(iter(state["observations"].values()))["origin"]["claim_id"] = "not-an-audit-pointer"
    with pytest.raises(LayoutError, match="reference fields"):
        verify_origins(state, snap["evidence_artifacts"])
