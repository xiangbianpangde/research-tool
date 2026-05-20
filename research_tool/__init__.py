"""research_tool —— 调研工具核心引擎 + SDK。

公开 API 依据 03-Python库接口设计.md。
"""

from __future__ import annotations

from pathlib import Path

from .config import load_config
from .errors import (
    ConfigValidationError,
    LLMError,
    ResearchToolError,
    SearchError,
    StageError,
)
from .llm import LLMClient, LLMConfig, MockLLMClient
from .models import (
    CleanerConfig,
    CleanResult,
    CollectorConfig,
    CollectResult,
    ExtractorConfig,
    ExtractResult,
    OrganizerConfig,
    OrganizeResult,
    PipelineConfig,
    PipelineResult,
    ReporterConfig,
    ReportResult,
    StageEvent,
)
from .pipeline import ResearchPipeline, create_pipeline
from .stages import Cleaner, Collector, Extractor, Organizer, Reporter

__version__ = "0.1.0"

__all__ = [
    "ResearchPipeline",
    "create_pipeline",
    "PipelineConfig",
    "PipelineResult",
    "StageEvent",
    "Collector",
    "Cleaner",
    "Extractor",
    "Organizer",
    "Reporter",
    "CollectorConfig",
    "CleanerConfig",
    "ExtractorConfig",
    "OrganizerConfig",
    "ReporterConfig",
    "CollectResult",
    "CleanResult",
    "ExtractResult",
    "OrganizeResult",
    "ReportResult",
    "LLMClient",
    "LLMConfig",
    "MockLLMClient",
    "load_config",
    "research",
    "quick_collect",
    "ResearchToolError",
    "ConfigValidationError",
    "StageError",
    "LLMError",
    "SearchError",
]


async def research(
    topic: str,
    work_dir: str | Path = "./research-output",
    llm_provider: str = "deepseek",
    llm_model: str | None = None,
    config_path: str | Path | None = None,
) -> PipelineResult:
    """一键调研便捷函数（03 §5）。"""
    overrides: dict = {
        "topic": topic,
        "work_dir": str(work_dir),
        "llm": {"provider": llm_provider},
    }
    if llm_model:
        overrides["llm"]["model"] = llm_model
    config = load_config(config_path, overrides=overrides)
    return await ResearchPipeline(config).run(topic)


async def quick_collect(
    topic: str, max_results: int = 8, work_dir: str | Path = "./research-output"
) -> list[dict]:
    """仅采集便捷函数（03 §5）。返回 sources 列表。"""
    cfg = CollectorConfig(max_results_per_engine=max_results)
    result = await Collector(cfg).run(topic, Path(work_dir) / "quick")
    return [s.model_dump() for s in result.sources]
