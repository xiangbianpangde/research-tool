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

Provider = Literal["openai", "deepseek", "ollama", "anthropic", "minimax"]
SearchEngine = Literal[
    "web",
    "arxiv",
    "tavily",
    "scholar",
    "semantic_scholar",
    "wikipedia",
    "github",
    "pubmed",
    "google_news",
    "openalex",
    "crossref",
    "cvpr",  # CVPRTalk：DBLP venue:CVPR 论文源（ee 常指向 CVF Open Access / arXiv）
    "bilibili",  # V1.1 落地（GAP-V1）：通过 yt-dlp BiliSearch extractor 搜 B 站视频
    "youtube",  # CVPRTalk：yt-dlp ytsearch 发现 YouTube 视频（ingest 另走 video_pipeline）
    "x",
    "twitter",
]
ExtractTask = Literal["ner", "re", "triple"]
StageName = Literal["collect", "deepen", "clean", "extract", "organize", "report"]
ResearchMode = Literal["fast", "standard", "deep"]


class LLMConfig(BaseModel):
    """LLM 连接配置。依据 04 §1 llm 段。"""

    provider: Provider = "deepseek"
    model: str = "deepseek-chat"
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)


class ExpertEntry(BaseModel):
    """专家库单条目（ExpertLib）。策展的可信实体，让其产出绕过搜索默认排序被保证纳入。"""

    id: str
    name: str = ""
    kind: Literal["org", "author", "lab"] = "org"
    # 领域标签：与 topic 做词重叠匹配的键（手工策展，v1 不上语义匹配）
    domains: list[str] = Field(default_factory=list)
    # 跨平台句柄：github（org/user）、arxiv_author、scholar、homepage、youtube 等
    handles: dict[str, str | None] = Field(default_factory=dict)
    # 强档：已知高质量但易被埋没的具体产出 URL，直塞抓取队列（并入 extra_urls），跳过搜索
    seed_urls: list[str] = Field(default_factory=list)
    # high 命中豁免 top-N 截断且排序靠前；normal 仅豁免
    priority: Literal["high", "normal"] = "normal"
    notes: str = ""


class ExpertLibrary(BaseModel):
    """专家库根对象，对应 experts.yaml。"""

    version: int = 1
    experts: list[ExpertEntry] = Field(default_factory=list)


class CollectorConfig(BaseModel):
    """采集配置。依据 01 §2.3 + 04 collector 段。"""

    search_engines: list[SearchEngine] = Field(default_factory=lambda: ["web"])
    max_results_per_engine: int = Field(default=8, gt=0)
    depth: int = Field(default=2, ge=1, le=3)
    language: Literal["zh", "en", "both"] = "both"
    concurrency: int = Field(default=4, gt=0)  # 抓取层并发
    # 搜索层并发上限：限制 engine×query 同时发出的请求数，缓解 DDG/Tavily 限流
    max_concurrent_searches: int = Field(default=3, gt=0)
    timeout_sec: int = Field(default=30, gt=0)
    # HTTP/SOCKS5 代理：国内访问 DDG/Wikipedia/GoogleNews/Tavily 等被墙站点需设。
    # 支持 http:// https:// socks5://；openalex/crossref/arxiv/pubmed/bilibili 国内直连可用。
    # 在 .env 设 HTTPS_PROXY，config.yaml 引用 ${HTTPS_PROXY}；留空则仅国内可达源正常。
    proxy: str | None = None
    tavily_api_key: str | None = None
    # 可选 Key：提升对应后端配额（无 Key 也能用，仅限流更严）
    semantic_scholar_api_key: str | None = None
    github_token: str | None = None
    # 有 token 时启用 /search/code 补「代码命中」仓库（无 token 自动跳过）
    github_code_search: bool = True
    # 邮箱（可选）：OpenAlex/Crossref 的 polite pool，填了限流更宽更稳
    openalex_mailto: str | None = None
    # 多轮搜索（方法论 1.1）：1=仅核心词 2=+交叉/相关概念 3=+补充细化
    search_rounds: int = Field(default=1, ge=1, le=3)
    max_total_results: int = Field(default=40, gt=0)  # 多轮去重后的总量上限
    # 用 LLM 动态生成贴主题的多轮查询（替代模板扩展，需提供 llm）
    llm_query_expansion: bool = False
    # 用户显式提供的额外查询（最高优先级，直接并入搜索；适合点名要找的论文/方法）
    extra_queries: list[str] = Field(default_factory=list)
    # 核心词（P1）：两阶段搜索的去锚锚点。设置后 Phase2 用它（而非含机构名的
    # 整条 topic）展开查询，突破"被单一机构/限定语绑架"的偏差。如 topic=
    # "中南民族大学 康怡琳" + core_keyword="康怡琳"。
    core_keyword: str | None = None
    # 维度标签（P1）：仅在 Phase2（去锚）生效，与 core_keyword 组合展开查询维度，
    # 如 ["博士", "论文", "南洋理工"]。Phase1（锚定）不加 facets，保证窄查询精准。
    facets: list[str] = Field(default_factory=list)
    # 时间标签（P2）：限定发表年份窗口（含起止）。None=不限。
    # 学科调研统一过滤；人物调研一般不设（让 deepen 按画像时间线逐节点过滤）。
    # 仅支持原生过滤的源生效（openalex/s2/crossref/pubmed），arxiv 客户端过滤，
    # web/wikipedia 忽略。
    from_year: int | None = None
    to_year: int | None = None
    # Deep-Search 深搜（P2）：多排序策略 × 多页翻页，突破"单次只取第1页相关性排序"
    # 的覆盖不足。每个 (engine, query) 展开为 deep_pages × deep_sorts 次搜索后去重。
    deep_search: bool = False
    deep_pages: int = Field(default=3, ge=1, le=10)  # 翻页页数
    deep_sorts: list[str] = Field(  # 排序策略，relevance=后端默认相关性
        default_factory=lambda: ["relevance", "date", "citations"]
    )
    # 搜索结果磁盘缓存（缓解 arxiv 等限流；按 engine+query 哈希）
    search_cache: bool = True
    cache_dir: str | None = None  # None=~/.research/cache/search
    cache_ttl_sec: int = Field(default=86400, ge=0)  # 缓存有效期，0=永不过期
    # 抓到 PDF 时用 MinerU 解析为正文（否则跳过，绝不把二进制塞进 raw）
    parse_pdf: bool = True
    mineru_cmd: str | None = None  # mineru 可执行路径，None=走 PATH
    # 垃圾过滤：抓取正文短于此字符数的结果直接丢弃（登录页/导航页等）
    min_doc_chars: int = Field(default=200, ge=0)
    # 轻量搜索结果相关性过滤：在抓取前按 topic/query 与 title/snippet 的词重叠剔除
    # 明显跑偏的命中。0=关闭；默认保守开启，避免 OpenAlex/Crossref 混入离题 PDF。
    search_relevance_min_overlap: float = Field(default=0.12, ge=0.0, le=1.0)
    # X/Twitter 搜索后端：默认 opencli；也可 twitter-cli。
    # 使用前需 opencli doctor 或 twitter status 通过（见 x_backend.preflight_x）。
    x_backend: Literal["twitter-cli", "opencli"] = "opencli"
    x_cmd: str = "twitter"
    # X 结果最少互动量（likes+rts+replies 合计）；0=不过滤。CLI 字段缺失时跳过门槛。
    x_min_engagement: int = Field(default=0, ge=0)
    # 专家库（ExpertLib）：已知 URL 直塞抓取队列，跳过搜索、绕过默认排序，保证纳入。
    # 专家 seed_urls 与 CVPR 论文的已知 arXiv 链接共用此 hook（最高优先级）。
    extra_urls: list[str] = Field(default_factory=list)
    # 新论文官方来源：必须先成功抓取，再启动 OpenAlex/Crossref/arXiv 旁支扩展。
    official_urls: list[str] = Field(default_factory=list)
    # experts.yaml 路径；None=不启用专家库。文件缺失/损坏按空库回退，不崩管道。
    experts_file: str | None = None
    # topic 与专家 domains 的词重叠阈值（占 topic 词数比例），低于不算命中。
    expert_match_min_overlap: float = Field(default=0.15, ge=0.0, le=1.0)
    # 专家命中是否豁免各源 top-N 截断（额外保留，不占名额）。
    expert_exempt_topn: bool = True
    # 专家弱档：匹配到的 org 生成 org:<handle> <topic> 定向 github 搜索，结果 expert=True。
    expert_scoped_github: bool = True

    @model_validator(mode="before")
    @classmethod
    def _enable_bibliographic_extension_for_official_sources(cls, value):
        if not isinstance(value, dict) or not value.get("official_urls"):
            return value
        data = dict(value)
        engines = list(data.get("search_engines") or ["web"])
        for engine in ("openalex", "crossref", "arxiv"):
            if engine not in engines:
                engines = [*engines, engine]
        return {**data, "search_engines": engines}

    @model_validator(mode="after")
    def _proxy_empty_to_none(self) -> "CollectorConfig":
        # .env 里 HTTPS_PROXY= 空值会被 dotenv 加载为空串；空串传给 httpx/ddgs 会报错，
        # 视为未设置（None）--此时仅国内可达源（openalex/crossref/arxiv/pubmed/bilibili）正常。
        if isinstance(self.proxy, str) and not self.proxy.strip():
            self.proxy = None
        return self


class PdfIngestConfig(BaseModel):
    """PDF 摄取配置（pdf2zh/MinerU 集成）。"""

    ocr_engine: Literal[
        "auto", "mineru", "custom", "paddleocr-vl", "unlimited-ocr", "vision-llm"
    ] = "mineru"
    mineru_backend: Literal[
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    ] = "pipeline"
    ocr_lang: str = "en"  # MinerU OCR 语言提示
    mineru_cmd: str | None = None  # 自定义 mineru 可执行路径（默认走 PATH）
    ocr_cmd: str | None = None  # custom / model wrapper 命令；输出 Markdown 或写出 md
    ocr_model_path: str | None = None  # 本地模型目录（如 PaddleOCR-VL / Unlimited-OCR）
    vision_prompt: str = "Extract the document text as clean Markdown."
    start_page: int | None = None
    end_page: int | None = None
    translate: bool = False  # 是否把英文 MD 翻译成中文（可选）
    translate_chunk_size: int = Field(default=3000, gt=0)
    translate_concurrency: int = Field(default=8, gt=0)


class CleanerConfig(BaseModel):
    """清洗配置。依据 01 §3.3 + 04 cleaner 段。"""

    strip_html: bool = True
    strip_nav: bool = True
    strip_ads: bool = True
    find_content_start: bool = True
    min_content_length: int = Field(default=200, ge=0)
    # MinHash 去重（P2-5）：char n-gram Jaccard 相似度 ≥阈值视为重复，组内保留
    # 最长文本，其余标 dedup_of。0=关闭去重。
    dedup_similarity: float = Field(default=0.85, ge=0.0, le=1.0)
    # LLM 相关性过滤（P2-5）：批量对清洗后文档评 0-1 分，低分剔出 clean/（raw/
    # 保留以便溯源）。默认关——开启会显著增加 LLM 调用。
    relevance_filter: bool = False
    relevance_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    relevance_batch_size: int = Field(default=10, gt=0)


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
    # 反向传播质量评估（P2-6）：节点正文中 "来源NN" 引用数 <此 → 判为稀疏节点
    min_evidence_per_node: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _check_node_bounds(self) -> "OrganizerConfig":
        if self.min_nodes > self.max_nodes:
            raise ValueError(f"min_nodes ({self.min_nodes}) 不能大于 max_nodes ({self.max_nodes})")
        return self


class ReporterConfig(BaseModel):
    """报告配置。依据 01 §6.3 + 04 reporter 段。"""

    format: Literal["markdown", "html"] = "markdown"
    style: Literal["report", "feasibility", "review", "article"] = "report"
    max_length: int = Field(default=30000, gt=0)


class DeepenConfig(BaseModel):
    """反偏差深挖配置（Phase 2）。依据升级计划 §4。"""

    enabled: bool = True  # false 或 --skip deepen 跳过深挖
    # 画像提取（P1）：从 raw/ 摘要让 LLM 抽结构化画像（中英文名/机构/领域），
    # 再据此生成注入查询（如补"英文名 NTU"）。失败回退现行实体查询。
    profile_extract: bool = True
    # 画像迭代轮数（P2-4）：=1 仅 P1 单轮；≥2 启用 timeline 回溯 + 同名消歧 + 重抽画像循环
    profile_iterations: int = Field(default=1, ge=1, le=5)
    min_new_files_per_iter: int = Field(default=2, ge=0)  # 本轮新增文件 <此 → 提前终止
    min_profile_confidence: float = Field(default=0.85, ge=0.0, le=1.0)  # 画像置信度达此 → 终止
    timeline_backtrack: bool = True  # 对画像每段经历做带时间窗口的回溯搜索
    disambiguation: bool = (
        True  # 同名消歧：LLM 判每份资料是否属于核心实体，他人的移到 raw/_disambig/
    )
    depth: int = Field(default=2, ge=1, le=3)  # 深挖轮次
    breadth: int = Field(default=4, ge=2, le=8)  # 每轮补充查询数上限
    entity_split: bool = True  # 拆分多实体话题（人物/组织等）
    max_entities: int = Field(default=5, ge=1, le=8)  # 实体拆分上限（风险 7）
    gap_detection: bool = True  # 扫描已采内容识别缺失维度
    contradiction_check: bool = True  # 检测矛盾并反向验证
    # 喂给 LLM 做缺口分析时的上下文截断（风险 4：每文件取标题+摘要，总量封顶）
    max_input_chars: int = Field(default=20000, gt=0)
    per_file_chars: int = Field(default=300, gt=0)


class TalkConfig(BaseModel):
    """CVPRTalk：论文 → YouTube 演讲视频关联（organize 后 enrichment）。

    默认关闭。开启后：从 raw/sources.json 抽论文候选 → YouTube 搜 talk →
    标题相似度置信闸 → 写 discovery 笔记（可选全量 VideoIngest）。
    """

    enabled: bool = False  # 对应 CLI --with-talks
    max_talks: int = Field(default=5, ge=0, le=50)  # 硬上限，防 whisper 成本爆炸
    min_title_similarity: float = Field(default=0.7, ge=0.0, le=1.0)  # 置信闸
    prefer_official_channel: bool = True  # 优先 CVF/官方频道候选
    # False：只写 discovery 笔记到 raw/（零 whisper 成本）；True：调用 process_videos
    ingest: bool = False
    conference: str = "CVPR"  # 搜索查询里拼接的会议名
    search_results_per_paper: int = Field(default=5, ge=1, le=15)


class PipelineConfig(BaseModel):
    """管道总配置。依据 01 §7.3 + 03 §2。"""

    topic: str = ""
    mode: ResearchMode = "standard"
    work_dir: Path = Path("./research-output")
    stages: list[StageName] = Field(
        default_factory=lambda: ["collect", "deepen", "clean", "extract", "organize", "report"]
    )
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    deepen: DeepenConfig = Field(default_factory=DeepenConfig)
    pdf_ingest: PdfIngestConfig = Field(default_factory=PdfIngestConfig)
    pdf_dir: str | None = None  # 设置后 collect 阶段改为摄取该目录下的 PDF
    cleaner: CleanerConfig = Field(default_factory=CleanerConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    organizer: OrganizerConfig = Field(default_factory=OrganizerConfig)
    reporter: ReporterConfig = Field(default_factory=ReporterConfig)
    talk: TalkConfig = Field(default_factory=TalkConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    resume: bool = True  # 幂等跳过已完成 Stage（05 §5）
    # 反向传播（P2-6）：完成一次正向后，让 organizer 评估知识树质量，把稀疏节点/
    # 知识断层/矛盾产出修正查询回到 collect 重跑。0=不启用（向后兼容）。
    max_backward_rounds: int = Field(default=0, ge=0, le=3)


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
    # V1.1：保留搜索后端附带的结构化片段（UP主/时长/简介），
    # 让桥接脚本可以做 author/duration 过滤而不丢信息。默认空，向后兼容。
    snippet: str = ""


class SourceAudit(BaseModel):
    """Per-engine collection funnel persisted in ``raw/source-audit.json``."""

    engine: str
    attempted: int = Field(default=0, ge=0)
    hits: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    filtered: int = Field(default=0, ge=0)
    deduplicated: int = Field(default=0, ge=0)
    fetch_failed: int = Field(default=0, ge=0)
    retained: int = Field(default=0, ge=0)


class CollectResult(BaseModel):
    files: list[Path] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    raw_dir: Path
    # 搜索后端失败/被丢弃的提示（修复 1：可观测，不再静默）
    warnings: list[str] = Field(default_factory=list)
    source_audits: list[SourceAudit] = Field(default_factory=list)


class DeepenResult(BaseModel):
    """深挖阶段产出统计。"""

    entities: list[str] = Field(default_factory=list)  # 拆出的实体
    queries: list[str] = Field(default_factory=list)  # 实际执行的补充查询
    new_files: list[Path] = Field(default_factory=list)  # 新增 raw 文件
    warnings: list[str] = Field(default_factory=list)


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


class FeedbackPlan(BaseModel):
    """P2-6 反向传播：知识树质量评估后产出的修正查询计划。"""

    sparse_nodes: list[str] = Field(default_factory=list)  # 证据不足的节点标题
    queries: list[str] = Field(default_factory=list)  # 用于回到 collect 的修正查询
    notes: list[str] = Field(default_factory=list)  # 矛盾/断层等观察


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


# --------------------------------------------------------------------------- #
# V1.1 VideoIngest 新增模型（依据 DD-001:DE-001~DE-010）
# 追加在末尾，不修改既有类，避免破坏 V1.0 测试断言
# --------------------------------------------------------------------------- #


VideoPlatform = Literal["youtube", "bilibili", "local"]


class VideoURL(BaseModel):
    """统一视频 URL 表示（DE-001）。

    平台无关的 URL 包装；platform 字段决定走哪个适配器。
    """

    platform: VideoPlatform
    url: str
    video_id: str | None = None  # 平台侧 id（BV号 / YouTube 11位）


class DownloadTask(BaseModel):
    """下载结果（DE-005）。

    file_path 必填（即使本地文件也是已解析的绝对路径）；
    etag 用于 M-004 缓存键的次级区分。
    """

    file_path: str
    size_mb: float = 0.0
    duration_sec: int = 0
    video_id: str = ""
    etag: str = ""
    platform: str = ""
    title: str = ""
    cover_url: str | None = None


class VideoMeta(BaseModel):
    """视频元数据（DE-002）。"""

    video_id: str
    platform: str
    title: str
    author: str = ""
    duration_sec: int = 0
    url: str = ""
    cover_url: str | None = None
    language: str = "zh"  # 默认中文（V1.1 需求）


class TranscriptSegment(BaseModel):
    """转写段落（DE-009）。"""

    start: float
    end: float
    text: str


class Transcript(BaseModel):
    """转写结果（DE-008）。"""

    language: str = "zh"
    full_text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)
    engine: str = ""  # "minimax" / "whisper" / "groq" / "bcut"
    cer_estimate: float = 0.0
    raw: dict | None = None


class Chapter(BaseModel):
    """章节（DE-003）。"""

    start_sec: float
    end_sec: float
    title: str
    summary: str = ""


class LLMSummary(BaseModel):
    """LLM 总结输出（DE-006）。

    字段命名遵循 M-007 笔记输出契约：
    - video_summary  : 整段总结
    - video_chapters : 章节列表
    - video_takeaways: 关键要点
    """

    video_summary: str = ""
    video_chapters: list[Chapter] = Field(default_factory=list)
    video_takeaways: list[str] = Field(default_factory=list)
    model: str = ""  # 实际调用的 LLM 模型名


class ScreenshotFrame(BaseModel):
    """截图帧（DE-005 复用；V1.1 track-core 仅声明契约，不实际生成）。"""

    timestamp_sec: float
    path: str  # 相对路径或绝对路径
    caption: str = ""


class VideoIngestConfig(BaseModel):
    """VideoIngest V1.1 配置（DE-011 / DD-001）。"""

    # 输出根目录
    work_dir: Path = Path("./research-output/video")
    # 默认语言
    language: Literal["zh", "en", "ja"] = "zh"
    # M-005 转写引擎偏好：默认 MiniMax-M3 多模态（视频/图片理解 → 讲稿）；
    # 失败再 whisper → groq。MiniMax-M3 不接受纯音频，需视频文件或关键帧图。
    preferred_engine: Literal["minimax", "whisper", "groq"] = "minimax"
    # M-005 whisper 模型档位
    whisper_model_size: Literal["tiny", "base", "small", "medium", "large-v3"] = "medium"
    # M-005 降档目标（RAM < 8GB）
    whisper_fallback_sizes: list[str] = Field(default_factory=lambda: ["base", "small"])
    # M-003 重试次数
    download_retry_times: int = Field(default=1, ge=0, le=3)
    # M-005 转写超时（秒）
    transcribe_timeout_sec: int = Field(default=1800, ge=30)  # 默认 30 分钟
    # MiniMax（转写 + 总结；与 llm.api_key / ANTHROPIC_API_KEY 可共用）
    minimax_api_key: str | None = None
    minimax_model: str = "MiniMax-M3"
    # OpenAI 兼容端点根（含 /v1）；空则从环境或 api.minimaxi.com/v1 推断
    minimax_base_url: str | None = None
    # Groq API key（可选 fallback）
    groq_api_key: str | None = None
    # Cookie 文件路径（YouTube / Bilibili 通用；X 默认走 opencli 浏览器登录态，勿手填）
    cookie_path: str | None = None
    # ffmpeg 路径
    ffmpeg_path: str | None = None


class StageRunMetric(BaseModel):
    """One stage's wall-clock result, persisted in ``run-summary.json``."""

    stage: str
    status: Literal["completed", "failed", "skipped"]
    duration_sec: float = Field(default=0.0, ge=0.0)
    message: str = ""


class PipelineResult(BaseModel):
    topic_dir: Path
    stages_completed: list[str] = Field(default_factory=list)
    stages_skipped: list[str] = Field(default_factory=list)
    failed_stage: str | None = None
    elapsed_sec: float = 0.0
    stage_metrics: list[StageRunMetric] = Field(default_factory=list)
    run_summary_path: Path | None = None
    collect_result: CollectResult | None = None
    deepen_result: DeepenResult | None = None
    clean_result: CleanResult | None = None
    extract_result: ExtractResult | None = None
    organize_result: OrganizeResult | None = None
    report_result: ReportResult | None = None
