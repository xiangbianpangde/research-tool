"""Small final review counterexamples at CLI and graph envelope reread boundaries."""

import pytest
from research_tool.infrastructure.disposition import DispositionError
from research_tool.infrastructure.disposition.records import committed_files, validate_graph
from research_tool.infrastructure.disposition.commands import execute
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


def test_snapshot_invalid_request_is_clean_validation_error(tmp_path):
    with pytest.raises(ValueError, match="invalid fields"):
        execute("snapshot", tmp_path, {"not_an_argument": True})


@pytest.mark.asyncio
async def test_graph_envelope_reread_must_still_match_committed_hash(corpus):
    _, topic, summary, _, _, graph = corpus
    record, _, files = committed_files(topic, summary["run_id"])
    entry = record["runtime"]["completed_stages"]["knowledge"]
    path = topic / entry["artifact_path"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(DispositionError, match="stage"):
        validate_graph(topic, record, files, graph)
