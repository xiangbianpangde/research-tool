"""research_tool —— 调研工具核心引擎 + SDK。

公开 API 依据 03-Python库接口设计.md。
"""

from __future__ import annotations

from pathlib import Path as _Path

# 在导入任何业务模块前加载 .env（V1.1：让 .env 里的 API key / FFMPEG_PATH 自动可用）
# 失败静默：python-dotenv 未装、.env 不存在都不应阻断 import。
try:
    import os as _os
    from dotenv import load_dotenv as _load_dotenv  # type: ignore[import-not-found]

    _env_path = _Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
    # 跨平台：.env 里常写小写 key（如 minimax_api_key），把它们镜像成大写一份，
    # 让 _PROVIDER_KEY_ENV 这类只查大写名的代码在 Linux 上也能拿到值。
    for _k in list(_os.environ.keys()):
        _kl = _k.lower()
        if (
            _kl.endswith("_api_key")
            or _kl.endswith("_api_url")
            or _kl in ("ffmpeg_path", "minimax_model")
        ):
            _os.environ.setdefault(_k.upper(), _os.environ[_k])
except Exception:  # noqa: S110  # .env autoload — best-effort, must not block import
    pass

from pathlib import Path

from .domain.config import load_config
from .domain.errors import (
    ConfigValidationError,
    LLMError,
    ResearchToolError,
    SearchError,
    StageError,
)
from .infrastructure.llm import LLMClient, LLMConfig, MockLLMClient
from .domain.models import (
    CleanerConfig,
    CleanResult,
    CollectorConfig,
    CollectResult,
    ExtractorConfig,
    ExtractResult,
    OrganizerConfig,
    OrganizeResult,
    PdfIngestConfig,
    PipelineConfig,
    PipelineResult,
    ReporterConfig,
    ReportResult,
    StageEvent,
)
from .infrastructure.ingest import PdfIngestor, ingest_pdfs
from .application.pipeline import ResearchPipeline, create_pipeline
from .infrastructure.stages import Cleaner, Collector, Extractor, Organizer, Reporter
from .common.translate import translate_markdown

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
    "PdfIngestor",
    "ingest_pdfs",
    "translate_markdown",
    "PdfIngestConfig",
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
