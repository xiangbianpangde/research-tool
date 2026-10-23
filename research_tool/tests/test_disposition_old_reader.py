"""A15 old manifest/v1 consumer must reject reviewed v2 rather than ignore required fields."""

import io
import json
import subprocess
import tarfile
import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import DispositionConfig, RetrievalConfig
from research_tool.infrastructure.disposition import Registry
from research_tool.infrastructure.retrieval import ResearchCtlClient
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_researchctl_grounded_contract import module_root
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401


@pytest.mark.asyncio
async def test_old_pinned_v1_reader_rejects_reviewed_manifest(tmp_path, external_io):
    cfg = config(tmp_path / "work")
    reg = Registry.create(
        tmp_path / "registry", registry_id="old-reader", roots={"root": str(tmp_path)}
    )
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    result = await ResearchPipeline(cfg, llm=MockLLMClient(structured_response=model_reply)).run()
    assert result.verdict == "STOP_SUCCESS"
    proc = subprocess.run(  # noqa: S603 - fixed trusted git archive, no shell
        [  # noqa: S607 - existing system git used with pinned repository commit
            "git",
            "-C",
            str(module_root()),
            "archive",
            "4189ede89ace5828f11d6968153addf12b253a1c",
            "researchctl",
        ],
        capture_output=True,
        check=True,
    )
    old = tmp_path / "old-reader"
    old.mkdir()
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        archive.extractall(old, filter="data")
    consumer = ResearchCtlClient(result.topic_dir, module_root=old)
    assert "issue-evaluation/v1" not in consumer.capabilities()["contracts"]
    assert consumer.index()["status"] == "fail_closed"
    summary = json.loads(result.run_summary_path.read_text())
    manifest = json.loads(
        (result.topic_dir / "runs" / summary["run_id"] / "retrieval-manifest.json").read_text()
    )
    assert manifest["schema_version"] == 2
