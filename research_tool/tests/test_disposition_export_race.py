"""Re-reading mutable files after a verified pass still requires the original pin."""

import pytest
from research_tool.infrastructure.disposition import Registry, DispositionError
from research_tool.tests.test_disposition_records import corpus as corpus  # noqa: F401
from research_tool.tests.test_disposition_transitions import imported
from research_tool.tests.test_nine_loop_runner import external_io as external_io  # noqa: F401


@pytest.mark.asyncio
async def test_novelty_baseline_run_must_equal_original_run_record(corpus, monkeypatch):
    reg, issue, _, _ = imported(corpus)
    _, state, _ = reg._read()
    from research_tool.infrastructure.disposition import registry as module

    original = module.committed_files

    def swapped(root, run_id):
        record, _, files = original(root, run_id)
        return record, "0" * 64, files

    monkeypatch.setattr(module, "committed_files", swapped)
    with pytest.raises(DispositionError, match="run identity"):
        reg._validate_new_evidence(state, issue, {"acceptance_evidence": []})


def test_export_rechecks_revision_after_verified_read(tmp_path, monkeypatch):
    reg = Registry.create(
        tmp_path / "registry", registry_id="export", roots={"root": str(tmp_path)}
    )
    from research_tool.infrastructure.disposition import registry as module

    original = module.safe_bytes
    reads = 0

    def changed(root, path):
        nonlocal reads
        data = original(root, path)
        if path.startswith("revisions/"):
            reads += 1
            if reads == 2:
                return data + b" "
        return data

    monkeypatch.setattr(module, "safe_bytes", changed)
    with pytest.raises(DispositionError, match="export"):
        reg.export(expected_head=reg.head())
