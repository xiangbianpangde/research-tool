"""Ledger replay optimization must preserve functional transition isolation and all byte pins."""

import copy
import pytest
from research_tool.infrastructure.disposition import Registry, DispositionError
from research_tool.infrastructure.disposition.state import apply_event


def test_verified_warm_replay_still_rejects_changed_history_and_isolates_returned_state(tmp_path):
    reg = Registry.create(tmp_path / "registry", registry_id="warm", roots={"data": str(tmp_path)})
    head, state, events = reg._read()
    original_events = copy.deepcopy(events)
    state["registry_id"] = "caller-mutation"
    events[0].pop("event_id")
    events[0]["payload"]["registry_id"] = "nested-caller-mutation"
    assert reg._read()[1]["registry_id"] == "warm"
    assert reg._read()[2] == original_events
    path = reg.root / "revisions" / (head + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(DispositionError, match="hash"):
        reg._read()


def test_transition_does_not_mutate_prior_state_or_shared_network():
    init = {"type": "initialize", "payload": {"registry_id": "r", "roots": {"data": "/not/read"}}}
    state = apply_event(None, init)
    state["network"] = {"large_immutable_mapping": [{"scope": {"outcome": "same"}}] * 100}
    original = copy.deepcopy(state)
    updated = apply_event(
        state, {"type": "checkpoint", "payload": {"reason": "Only history changed"}}
    )
    assert state == original
    assert updated["network"] == state["network"]
    updated["history"].append({"other": "x"})
    assert state["history"] == []
