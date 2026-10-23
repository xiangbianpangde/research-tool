"""Independent reader rejects self-consistent but unauthoritative projection/review inputs."""

import sys
import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_current_authoritative_inputs_cannot_be_omitted_from_evaluation(corpus):
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evaluation import verify
    from researchctl.layouts.research_tool import LayoutError

    reg, _, _, graph = imported(corpus)
    evaluation = evaluate_registry(reg.root, reg.head(), {"findings": []}, graph)
    with pytest.raises(LayoutError, match="limitation"):
        verify(
            evaluation,
            graph,
            {"raw_findings": [], "known_limitations": ["New requirement not imported"]},
        )
    finding = next(
        o["original"] for o in reg.view()["observations"].values() if o["kind"] == "finding"
    )
    with pytest.raises(LayoutError, match="finding"):
        verify(evaluation, graph, {"raw_findings": [finding], "known_limitations": []})
    from researchctl.layouts.issue_evidence import verify_origins
    import copy

    altered = copy.deepcopy(evaluation["decision_snapshot"]["state"])
    next(iter(altered["observations"].values()))["required"] = False
    with pytest.raises(LayoutError, match="requirement"):
        verify_origins(altered, evaluation["decision_snapshot"]["evidence_artifacts"])


def test_reader_review_request_must_match_applied_payload():
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_ledger import _request_binding
    from researchctl.layouts.research_tool import LayoutError

    applied = {
        "proposal_id": "p",
        "proposal_sha256": "a" * 64,
        "approve": True,
        "reason": "Approved",
        "authority": "agent_source_review",
    }
    with pytest.raises(LayoutError):
        _request_binding("review", {}, applied)
    denied = {**applied, "approve": False}
    with pytest.raises(LayoutError):
        _request_binding("review", denied, applied)
