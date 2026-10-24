"""Reviewed semantics may neither silently disappear nor use unmatched prompt identities."""

from research_tool.infrastructure.disposition.reporting import prompt_semantics


def test_prompt_semantics_maps_all_reference_fields_to_same_short_ids():
    packet = {
        "assignments": [
            {"claim_id": "long-a", "role": "method"},
            {"claim_id": "long-b", "role": "primary_result"},
        ],
        "edges": [
            {
                "from_claim_id": "long-a",
                "to_claim_id": "long-b",
                "evidence_claim_ids": ["long-a", "long-b"],
            }
        ],
        "assessment_groups": [
            {"claim_ids": ["long-a", "long-b"], "checks": [{"evidence_claim_ids": ["long-b"]}]}
        ],
    }
    projected = prompt_semantics(
        packet, {"long-a": "E1", "long-b": "E2"}, {"long-a": {}, "long-b": {}}
    )
    assert projected["assignments"][0]["claim_id"] == "E1"
    assert projected["assignments"][1]["claim_id"] == "E2"
    assert projected["edges"][0]["to_claim_id"] == "E2"
    assert projected["assessment_groups"][0]["checks"][0]["evidence_claim_ids"] == ["E2"]
    assert packet["assignments"][0]["claim_id"] == "long-a"
