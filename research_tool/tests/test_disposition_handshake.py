"""Reviewed publication requires a capable pinned consumer, even for model-support reports."""

import pytest
from research_tool.application.pipeline import ResearchPipeline
from research_tool.domain.models import DispositionConfig, RetrievalConfig
from research_tool.infrastructure.disposition import Registry
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.tests.test_nine_loop_runner import (
    config,
    model_reply,
    external_io as external_io,
)  # noqa: F401
from research_tool.tests.test_researchctl_grounded_contract import module_root


@pytest.mark.asyncio
async def test_reviewed_config_requires_explicit_reader_before_collect(tmp_path, external_io):
    cfg = config(tmp_path / "work")
    reg = Registry.create(
        tmp_path / "issues", registry_id="handshake", roots={"test": str(tmp_path)}
    )
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    llm = MockLLMClient(structured_response=model_reply)
    result = await ResearchPipeline(cfg, llm=llm).run()
    assert result.verdict == "STOP_ERROR"
    assert llm.calls == [] and external_io == []


@pytest.mark.asyncio
async def test_selected_reader_missing_issue_contract_is_rejected(
    tmp_path, external_io, monkeypatch
):
    cfg = config(tmp_path / "work")
    reg = Registry.create(
        tmp_path / "issues", registry_id="handshake", roots={"test": str(tmp_path)}
    )
    cfg.disposition = DispositionConfig(
        policy="reviewed", registry_root=reg.root, expected_head_sha256=reg.head()
    )
    cfg.retrieval = RetrievalConfig(module_root=module_root())
    monkeypatch.setattr(
        "research_tool.infrastructure.retrieval.ResearchCtlClient.capabilities",
        lambda _: {"status": "success", "contracts": ["evidence-snapshot/v1"]},
    )
    llm = MockLLMClient(structured_response=model_reply)
    result = await ResearchPipeline(cfg, llm=llm).run()
    assert result.verdict == "STOP_ERROR" and llm.calls == []
