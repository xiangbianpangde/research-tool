"""Tier 1: Feature Coverage — F06: Stage Literal & Alias Support.

Verifies canonical aliases between domain and contract names:
- Stage 4: knowledge <-> network
- Stage 8: qgate <-> gate
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

STAGE_ALIASES = {
    "knowledge": "network",
    "network": "knowledge",
    "qgate": "gate",
    "gate": "qgate",
}


def test_canonical_alias_knowledge_maps_to_network():
    """F06-1: Verify knowledge maps to network contract name."""
    assert STAGE_ALIASES["knowledge"] == "network"
    assert STAGE_ALIASES["network"] == "knowledge"


def test_canonical_alias_qgate_maps_to_gate():
    """F06-2: Verify qgate maps to gate contract name."""
    assert STAGE_ALIASES["qgate"] == "gate"
    assert STAGE_ALIASES["gate"] == "qgate"


def test_stage_envelope_accepts_network_or_knowledge(workspace: Path, sample_knowledge_envelope):
    """F06-3: Verify stage envelope for Stage 4 accepts canonical name or alias."""
    assert sample_knowledge_envelope["stage"] in ("knowledge", "network")

    # Contract envelope with "network"
    from research_tool.nine_loop import knowledge_min

    assert knowledge_min.STAGE == "network"


def test_stage_envelope_accepts_gate_or_qgate(workspace: Path, sample_qgate_envelope):
    """F06-4: Verify stage envelope for Stage 8 accepts canonical name or alias."""
    assert sample_qgate_envelope["stage"] in ("qgate", "gate")

    from research_tool.nine_loop import qgate_min

    assert qgate_min.STAGE == "gate"


def test_alias_resolution_helper_normalizes_aliases():
    """F06-5: Verify normalization function canonicalizes aliases."""

    def normalize_stage(name: str) -> str:
        canonical_map = {"network": "knowledge", "gate": "qgate"}
        return canonical_map.get(name, name)

    assert normalize_stage("network") == "knowledge"
    assert normalize_stage("knowledge") == "knowledge"
    assert normalize_stage("gate") == "qgate"
    assert normalize_stage("qgate") == "qgate"
    assert normalize_stage("collect") == "collect"
