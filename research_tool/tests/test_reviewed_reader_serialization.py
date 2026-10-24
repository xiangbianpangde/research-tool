"""Real report JSON canonicalization must not turn object member ordering into an obligation mismatch."""

import json
import sys
import pytest
from research_tool.infrastructure.disposition.evaluation import evaluate_registry
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported, decide
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_reader_verifies_serialized_child_issue_set_not_object_iteration_order(corpus):
    reg, issue, _, graph = imported(corpus)
    text = issue["scope"]["target_requirement"]
    parts = [text[: len(text) // 2], text[len(text) // 2 :]]
    decide(
        reg,
        issue,
        "decomposed",
        {
            "rationale": "All original clauses preserved",
            "parts": [
                {"scope": {**issue["scope"], "target_requirement": p}, "coverage_text": p}
                for p in parts
            ],
        },
        "split",
    )
    value = evaluate_registry(reg.root, reg.head(), {"findings": []}, graph)
    serialized = json.loads(json.dumps(value, sort_keys=True))
    sys.path.insert(0, str(module_root()))
    from researchctl.layouts.issue_evaluation import verify
    from researchctl.layouts.research_tool import LayoutError

    verify(serialized, graph)
    serialized["active_required_issues"].pop()
    with pytest.raises(LayoutError):
        verify(serialized, graph)
