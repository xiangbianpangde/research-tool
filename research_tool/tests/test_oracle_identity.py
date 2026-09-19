"""Test PythonIdentityEngine against official rt-identity oracle test vectors.

Verifies 100% bit-identical normalization and deduplication against all 70
test vectors from oracle.v1.jsonl and oracle.v1.1.addendum.jsonl.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from research_tool.nine_loop.rt_identity_adapter import (
    AdapterClient,
    BatchResult,
    PythonIdentityEngine,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ORACLE_FILES = [
    FIXTURES_DIR / "oracle.v1.jsonl",
    FIXTURES_DIR / "oracle.v1.1.addendum.jsonl",
]


def _load_oracle_cases():
    cases = []
    for oracle_path in ORACLE_FILES:
        if not oracle_path.exists():
            ref_path = (
                Path("/Users/xbpd/Projects/research-tool-refactor/crates/rt-identity/fixtures")
                / oracle_path.name
            )
            if ref_path.exists():
                oracle_path = ref_path
        if not oracle_path.exists():
            continue
        with open(oracle_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                cases.append((record["id"], record["request"], record["expected_response"]))
    return cases


ALL_ORACLE_CASES = _load_oracle_cases()


def test_oracle_cases_loaded():
    """Verify all 70 oracle test vectors (66 in v1, 4 in v1.1 addendum) are loaded."""
    assert len(ALL_ORACLE_CASES) == 70, f"Expected 70 cases, got {len(ALL_ORACLE_CASES)}"


@pytest.mark.parametrize("case_id,request_obj,expected_response", ALL_ORACLE_CASES)
def test_python_identity_engine_oracle_vector(case_id, request_obj, expected_response):
    """Test PythonIdentityEngine.dispatch against single oracle vector."""
    actual = PythonIdentityEngine.dispatch(request_obj)
    assert actual == expected_response, (
        f"Case {case_id} mismatch:\n"
        f"Actual:   {json.dumps(actual, ensure_ascii=False)}\n"
        f"Expected: {json.dumps(expected_response, ensure_ascii=False)}"
    )


def test_python_identity_engine_batch_run():
    """Test batch run of all requests produces identical responses in order."""
    engine = PythonIdentityEngine()
    requests = [case[1] for case in ALL_ORACLE_CASES]
    expected_records = [case[2] for case in ALL_ORACLE_CASES]

    result: BatchResult = engine.run(requests)
    assert len(result) == len(expected_records)
    assert result.exit_code == 0
    assert result.records == expected_records


def test_adapter_client_fallback_execution():
    """Test AdapterClient executes without binary using PythonIdentityEngine fallback."""
    client = AdapterClient(binary_path=None, fallback=True)
    sample_request = ALL_ORACLE_CASES[0][1]
    expected = ALL_ORACLE_CASES[0][2]

    res = client.run([sample_request])
    assert len(res) == 1
    assert res.records[0] == expected
