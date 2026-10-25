"""Registered source conflicts must be visible beside every overlapping observation, not only a global list."""

from research_tool.nine_loop.grounded_synthesis import overlapping_source_conflicts


def test_long_context_atom_cannot_hide_a_registered_conflicting_subspan():
    def fact(cid, start, end):
        return {
            "claim_id": cid,
            "text": "literal " + cid,
            "citations": [
                {
                    "source_id": "s",
                    "content_sha256": "a" * 64,
                    "start_offset": start,
                    "end_offset": end,
                }
            ],
        }

    facts = {
        f["claim_id"]: f
        for f in [
            fact("whole", 0, 100),
            fact("all-nine", 20, 30),
            fact("subgroup-twelve", 120, 130),
        ]
    }
    required = [{"kind": "source_conflict", "claim_ids": ["all-nine", "subgroup-twelve"]}]
    assert overlapping_source_conflicts(["whole"], facts, required) == [
        ["all-nine", "subgroup-twelve"]
    ]
    assert overlapping_source_conflicts(["all-nine"], facts, required) == [
        ["all-nine", "subgroup-twelve"]
    ]
