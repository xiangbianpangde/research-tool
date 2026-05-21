"""所有公开数据模型（Pydantic v2）。

配置模型对应 04-配置结构设计.md；结果模型对应 01-核心引擎设计.md 与
05-数据流与文件规范.md 的各 Stage 输入/输出契约。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- #
# 配置模型
# --------------------------------------------------------------------------- #

Provider = Literal["openai", "deepseek", "ollama", "anthropic"]
SearchEngine = Literal["web", "arxiv", "tavily", "scholar"]
ExtractTask = Literal["ner", "re", "triple"]
StageName = Literal["collect", "clean", "extract", "organize", "report"]


class LLMConfig(BaseModel):
    """LLM 连接配置。依据 04 §1 llm 段。"""

    provider: Provider = "deepseek"
    model: str = "deepseek-chat"
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)


class CollectorConfig(BaseModel):
    """采集配置。依据 01 §2.3 + 04 collector 段。"""

    search_engines: list[SearchEngine] = Field(default_factory=lambda: ["web"])
    max_results_per_engine: int = Field(default=8, gt=0)
    depth: int = Field(default=2, ge=1, le=3)
    language: Literal["zh", "en", "both"] = "both"
    concurrency: int = Field(default=4, gt=0)
    timeout_sec: int = Field(default=30, gt=0)
    tavily_api_key: str | None = None
    # 多轮搜索（方法论 1.1）：1=仅核心词 2=+交叉/相关概念 3=+补充细化
    search_rounds: int = Field(default=1, ge=1, le=3)
    max_total_results: int = Field(default=40, gt=0)  # 多轮去重后的总量上限
    # 用 LLM 动态生成贴主题的多轮查询（替代模板扩展，需提供 llm）
    llm_query_expansion: bool = False
    # 用户显式提供的额外查询（最高优先级，直接并入搜索；适合点名要找的论文/方法）
    extra_queries: list[str] = Field(default_factory=list)
    # 搜索结果磁盘缓存（缓解 arxiv 等限流；按 engine+query 哈希）
    search_cache: bool = True
    cache_dir: str | None = None  # None=~/.research/cache/search
    cache_ttl_sec: int = Field(default=86400, ge=0)  # 缓存有效期，0=永不过期
    # 抓到 PDF 时用 MinerU 解析为正文（否则跳过，绝不把二进制塞进 raw）
    parse_pdf: bool = True
    mineru_cmd: str | None = None  # mineru 可执行路径，None=走 PATH
    # 垃圾过滤：抓取正文短于此字符数的结果直接丢弃（登录页/导航页等）
    min_doc_chars: int = Field(default=200, ge=0)


class PdfIngestConfig(BaseModel):
    """PDF 摄取配置（pdf2zh/MinerU 集成）。"""

    mineru_backend: Literal[
        "pipeline", "vlm-auto-engine", "hybrid-auto-engine",
        "vlm-http-client", "hybrid-http-client",
    ] = "pipeline"
    ocr_lang: str = "en"            # MinerU OCR 语言提示
    mineru_cmd: str | None = None   # 自定义 mineru 可执行路径（默认走 PATH）
    start_page: int | None = None
    end_page: int | None = None
    translate: bool = False         # 是否把英文 MD 翻译成中文（可选）
    translate_chunk_size: int = Field(default=3000, gt=0)
    translate_concurrency: int = Field(default=8, gt=0)


class CleanerConfig(BaseModel):
    """清洗配置。依据 01 §3.3 + 04 cleaner 段。"""

    strip_html: bool = True
    strip_nav: bool = True
    strip_ads: bool = True
    find_content_start: bool = True
    min_content_length: int = Field(default=200, ge=0)


class ExtractorConfig(BaseModel):
    """抽取配置。依据 01 §4.3 + 04 extractor 段。"""

    enabled: bool = True
    tasks: list[ExtractTask] = Field(default_factory=lambda: ["ner", "triple"])
    entity_types: list[str] | None = None
    relation_types: list[str] | None = None
    chunk_size: int = Field(default=4000, gt=0)
    overlap: int = Field(default=200, ge=0)


class OrganizerConfig(BaseModel):
    """组织配置。依据 01 §5.3 + 04 organizer 段。"""

    max_nodes: int = Field(default=7, gt=0)
    min_nodes: int = Field(default=4, gt=0)
    node_template: str = "S1-S4"

    @model_validator(mode="after")
    def _check_node_bounds(self) -> "OrganizerConfig":
        if self.min_nodes > self.max_nodes:
            raise ValueError(
                f"min_nodes ({self.min_nodes}) 不能大于 max_nodes ({self.max_nodes})"
            )
        return self


class ReporterConfig(BaseModel):
    """报告配置。依据 01 §6.3 + 04 reporter 段。"""

    format: Literal["markdown", "html"] = "markdown"
    style: Literal["report", "feasibility", "review", "article"] = "report"
    max_length: int = Field(default=30000, gt=0)


class PipelineConfig(BaseModel):
    """管道总配置。依据 01 §7.3 + 03 §2。"""

    topic: str = ""
    work_dir: Path = Path("./research-output")
    stages: list[StageName] = Field(
        default_factory=lambda: ["collect", "clean", "extract", "organize", "report"]
    )
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    pdf_ingest: PdfIngestConfig = Field(default_factory=PdfIngestConfig)
    pdf_dir: str | None = None  # 设置后 collect 阶段改为摄取该目录下的 PDF
    cleaner: CleanerConfig = Field(default_factory=CleanerConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    organizer: OrganizerConfig = Field(default_factory=OrganizerConfig)
    reporter: ReporterConfig = Field(default_factory=ReporterConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    resume: bool = True  # 幂等跳过已完成 Stage（05 §5）


# --------------------------------------------------------------------------- #
# 结果模型
# --------------------------------------------------------------------------- #


class Source(BaseModel):
    """单条采集来源。对应 sources.json 一项（05 §3 Stage1）。"""

    url: str
    title: str = ""
    fetched_at: str = ""
    source_engine: str = ""
    content_hash: str = ""


class CollectResult(BaseModel):
    files: list[Path] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    raw_dir: Path


class FileQuality(BaseModel):
    original_size: int
    cleaned_size: int
    score: float
    issues: list[str] = Field(default_factory=list)


class CleanResult(BaseModel):
    files: list[Path] = Field(default_factory=list)
    quality_report: dict[str, FileQuality] = Field(default_factory=dict)
    clean_dir: Path


class Entity(BaseModel):
    name: str
    type: str
    source_file: str = ""
    source_line: int = 0
    confidence: float = 1.0


class Relation(BaseModel):
    subject: str
    predicate: str
    object: str
    source_file: str = ""
    confidence: float = 1.0


class Triple(BaseModel):
    head: str
    relation: str
    tail: str
    source_file: str = ""


class ExtractResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    triples: list[Triple] = Field(default_factory=list)
    schema_: dict | None = Field(default=None, alias="schema")
    output_dir: Path

    model_config = {"populate_by_name": True}


class OrganizeResult(BaseModel):
    main_table: Path
    nodes: list[Path] = Field(default_factory=list)
    cross_refs: dict = Field(default_factory=dict)
    tree_dir: Path


class ReportResult(BaseModel):
    report_path: Path
    word_count: int = 0
    source_count: int = 0


class StageEvent(BaseModel):
    """流式执行事件。依据 03 §2 StageEvent。"""

    stage: str
    status: Literal["started", "progress", "completed", "failed", "skipped"]
    progress: float = 0.0
    message: str = ""
    data: dict | None = None


class PipelineResult(BaseModel):
    topic_dir: Path
    stages_completed: list[str] = Field(default_factory=list)
    stages_skipped: list[str] = Field(default_factory=list)
    failed_stage: str | None = None
    elapsed_sec: float = 0.0
    collect_result: CollectResult | None = None
    clean_result: CleanResult | None = None
    extract_result: ExtractResult | None = None
    organize_result: OrganizeResult | None = None
    report_result: ReportResult | None = None
