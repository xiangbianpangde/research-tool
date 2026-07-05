"""Regression: the documented public SDK import path must work.

Pins Phase-2 drift item #1 (research_tool vs src package name) so it cannot
silently recur.
"""

from __future__ import annotations


def test_public_sdk_imports() -> None:
    import research_tool
    from research_tool import (
        MockLLMClient,
        ResearchPipeline,
        load_config,
        quick_collect,
        research,
    )

    assert hasattr(research_tool, "__version__")
    assert callable(research)
    assert callable(quick_collect)
    assert callable(ResearchPipeline)
    assert callable(MockLLMClient)
    assert callable(load_config)
