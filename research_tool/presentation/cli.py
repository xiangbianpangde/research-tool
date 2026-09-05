"""命令行接口。

依据 02-CLI接口设计.md。CLI 是薄封装，直接调用核心引擎；用户输出经 logging 模块，
规范化消息 → stdout（INFO，无时间戳），问题 → stderr（WARNING+，含时间戳+模块）。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..domain.config import load_config
from ..domain.errors import ErrorCode, ResearchToolError, VideoIngestError
from ..infrastructure.llm.base import LLMClient
from ..common.logging_config import get_logger, setup_logging
from ..common.url_guard import is_sensitive_url_key
from ..domain.models import (
    CleanerConfig,
    CollectorConfig,
    ExtractorConfig,
    OrganizerConfig,
    PdfIngestConfig,
    ReporterConfig,
)
from ..application.pipeline import ResearchPipeline
from ..common.slug import slugify
from ..infrastructure.stages import Cleaner, Collector, Extractor, Organizer, Reporter
from ..infrastructure.export.wiki_publisher import publish as publish_to_wiki
from ..infrastructure.export.wiki_stage import (
    PackageStageError,
    build_stage_package,
    plan_stage_package,
)

logger = get_logger(__name__)

app = typer.Typer(
    add_completion=False,
    help="research —— 给定主题，产出知识树/调研报告",
    no_args_is_help=True,
)

# rich Console 仅用于渲染 JSON/Table 等结构化输出（非日志消息）
_out = Console()

def _redact_proxy_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "***"
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "***"
    if not parsed.scheme or not host:
        return "***"
    display_host = f"[{host}]" if ":" in host else host
    netloc = f"{display_host}:{port}" if port is not None else display_host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _redact_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "***"
    if not host:
        return "***"
    display_host = f"[{host}]" if ":" in host else host
    netloc = f"{display_host}:{port}" if port is not None else display_host

    def _clean_pairs(component: str) -> str:
        pairs = parse_qsl(component, keep_blank_values=True)
        cleaned = [
            (key, "***" if is_sensitive_url_key(key) else item)
            for key, item in pairs
        ]
        return urlencode(cleaned)

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            _clean_pairs(parsed.query),
            _clean_pairs(parsed.fragment),
        )
    )


def _redact_nested_urls(value: object) -> object:
    if isinstance(value, dict):
        return {key: _redact_nested_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_nested_urls(item) for item in value]
    if isinstance(value, str):
        return _redact_url(value)
    return value


def _redact_config_data(data: dict) -> dict:
    """Return a redacted copy safe for terminal and CI logs."""
    sanitized = _redact_nested_urls(data)
    if not isinstance(sanitized, dict):
        return {}
    llm = dict(sanitized.get("llm") or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    collector = dict(sanitized.get("collector") or {})
    for secret_field in (
        "github_token",
        "tavily_api_key",
        "semantic_scholar_api_key",
    ):
        if collector.get(secret_field):
            collector[secret_field] = "***"
    if collector.get("proxy"):
        collector["proxy"] = _redact_proxy_url(collector["proxy"])
    return {**sanitized, "llm": llm, "collector": collector}

# 全局状态（由回调填充）
_state: dict = {"config_path": None, "verbose": False, "quiet": False}

_ADVANCED = "高级选项（低频；等价项可写入 config.yaml）"


def _version_cb(value: bool) -> None:
    if value:
        _out.print(f"research-tool {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    config: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    verbose: bool = typer.Option(False, "--verbose", help="输出调试信息"),
    quiet: bool = typer.Option(False, "--quiet", help="仅输出错误"),
    version: bool = typer.Option(
        False, "--version", callback=_version_cb, is_eager=True, help="显示版本号"
    ),
) -> None:
    _state.update(config_path=config, verbose=verbose, quiet=quiet)
    setup_logging(verbose=verbose, quiet=quiet)


def _fail(msg: str) -> typer.Exit:
    """输出错误信息到 stderr 并退出。"""
    logger.error("错误 %s", msg)
    raise typer.Exit(code=1)


def _find_repo_root(start: Path | None = None) -> Path | None:
    candidates = [start or Path.cwd(), Path(__file__).resolve().parent]
    for start_dir in candidates:
        cur = start_dir.resolve()
        for p in [cur, *cur.parents]:
            if (p / ".git").exists() or (p / "pyproject.toml").exists():
                return p
    return None


def _assert_safe_output_dir(output: Path | None, configured_dir: Path | None = None) -> None:
    """Zero Workspace Mutation 硬防护（P1-E 闭环）：
    禁止将输出目录指定为项目根目录、或仓库内任何源码/文档/测试目录。
    """
    target = output or configured_dir
    if target is None:
        return
    resolved_out = target.resolve()
    repo_root = _find_repo_root()
    if repo_root is not None:
        if resolved_out == repo_root:
            _fail(f"安全拦截：输出目录不能直接指向项目根目录（{repo_root}），请指定子目录（如 ./research-output）")
        if resolved_out.is_relative_to(repo_root):
            rel = resolved_out.relative_to(repo_root)
            top_part = rel.parts[0] if rel.parts else ""
            allowed_prefixes = ("research-output", "work", "tmp", "temp", "dist", "build", "out")
            if not any(top_part.startswith(prefix) for prefix in allowed_prefixes):
                _fail(f"安全拦截（Zero Workspace Mutation）：禁止向仓库源码/资产/文档目录（{top_part}）输出调研产物！请指定 ./research-output")
    else:
        if (resolved_out / "research_tool").exists() or (resolved_out / ".git").exists():
            _fail("安全拦截：输出目录不能直接指向项目根目录")


def _fail_video_ingest(exc: VideoIngestError) -> None:
    """VideoIngestError 失败路径：渲染 M-010 3 段式 + 仲裁退出码。

     已注册错误码 → format_error 三段式输出 + resolve_exit_code 仲裁退出码
    （403/401/404/400/500）；未注册错误码 → 降级为通用 _fail（exit 1），
     不让处理器自身崩溃。
    """
    from ..domain.errors import ConfigError, format_error, register_error, resolve_exit_code

    try:
        record = register_error(exc.code, context={"message": str(exc)})
    except ConfigError:
        # 未注册错误码 — 降级通用路径，不崩溃处理器。
        _fail(f"[{exc.code}] {exc}")
        return  # _fail 已 raise typer.Exit；此行不可达，保类型完整
    logger.error("[%s] %s", exc.code, exc)
    typer.echo(format_error(record), err=True)
    raise typer.Exit(code=resolve_exit_code([record]))


def _split_csv(value: str | None) -> list[str]:
    """逗号分隔字符串 → 去空白去空项列表。None/空串 → []。"""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _print_warnings(warnings: list[str]) -> None:
    """搜索/深挖告警输出到 stderr。"""
    for w in warnings or []:
        logger.warning(w)


def _print_source_audits(audits) -> None:
    """输出真实多来源漏斗，明确区分「调用」与「最终贡献」。"""
    if not audits:
        return
    logger.info("来源审计（调用/命中/失败/过滤/去重/抓取失败/保留）:")
    for audit in audits:
        logger.info(
            "  %-18s %d/%d/%d/%d/%d/%d/%d",
            audit.engine,
            audit.attempted,
            audit.hits,
            audit.failed,
            audit.filtered,
            audit.deduplicated,
            audit.fetch_failed,
            audit.retained,
        )


def input_topic_from_dir(path: Path) -> str:
    """从 raw/clean/extracted/tree 的父目录名推断主题（slug 形式）。"""
    p = Path(path)
    parent = p.parent if p.name in ("raw", "clean", "extracted", "tree") else p
    return parent.name.replace("-", " ")


def _make_llm(model: str | None = None) -> LLMClient:
    overrides = {"llm": {"model": model}} if model else None
    cfg = load_config(_state["config_path"], overrides=overrides)
    return LLMClient.from_config(cfg.llm)


async def _make_healthy_llm(model: str | None = None) -> LLMClient:
    llm = _make_llm(model)
    await llm.healthcheck()
    return llm


# --------------------------------------------------------------------------- #
# collect
# --------------------------------------------------------------------------- #
@app.command()
def collect(
    topic: str = typer.Argument(..., help="调研主题"),
    source: list[str] = typer.Option(["web"], "-s", "--source", help="搜索来源"),
    max_results: int = typer.Option(8, "-n", "--max-results"),
    depth: int = typer.Option(2, "-d", "--depth"),
    language: str = typer.Option("both", "-l", "--language"),
    concurrency: int = typer.Option(4, "-c", "--concurrency"),
    timeout: int = typer.Option(30, "-t", "--timeout"),
    rounds: int = typer.Option(1, "-r", "--rounds", help="搜索轮次 1-3（方法论多轮）"),
    llm_expand: bool = typer.Option(
        False, "--llm-expand", help="用 LLM 动态生成多轮查询（需配置 LLM）"
    ),
    query: list[str] = typer.Option(
        [], "-q", "--query", help="额外查询（可多次，点名要找的论文/方法，最高优先级）"
    ),
    official_url: list[str] = typer.Option(
        [],
        "--official-url",
        help="新论文官方来源（可多次）；成功抓取后才运行 OpenAlex/Crossref/arXiv",
    ),
    core: Optional[str] = typer.Option(
        None, "--core", help="核心词（去锚锚点），如 topic 含机构名时填人物名突破偏差"
    ),
    facets: Optional[str] = typer.Option(
        None, "--facets", help="维度标签，逗号分隔（仅去锚阶段生效），如 博士,论文,南洋理工"
    ),
    from_year: Optional[int] = typer.Option(
        None, "--from-year", help="发表年份下限（含），openalex/s2/crossref/pubmed 原生过滤"
    ),
    to_year: Optional[int] = typer.Option(None, "--to-year", help="发表年份上限（含）"),
    deep_search: bool = typer.Option(
        False, "--deep-search", help="深搜模式：多排序×多页翻页，突破单次第1页覆盖不足"
    ),
    deep_pages: int = typer.Option(
        3, "--deep-pages", help="深搜翻页页数 1-10（仅 --deep-search 生效）"
    ),
    deep_sorts: str = typer.Option(
        "relevance,date,citations",
        "--deep-sorts",
        help="深搜排序策略，逗号分隔：relevance/date/citations",
    ),
    search_relevance_min_overlap: float = typer.Option(
        0.12,
        "--search-relevance-min-overlap",
        help="抓取前轻量相关性阈值，0=关闭",
    ),
    x_backend: str = typer.Option(
        "opencli",
        "--x-backend",
        help="X/Twitter 后端：opencli 或 twitter-cli",
    ),
    x_cmd: str = typer.Option(
        "twitter",
        "--x-cmd",
        help="twitter-cli 命令名或路径（仅 --x-backend twitter-cli 时使用）",
    ),
    output: Path = typer.Option(Path("./research-output"), "-o", "--output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅搜索不抓取"),
) -> None:
    """阶段1：搜索并抓取原始资料。"""
    _assert_safe_output_dir(output)
    cfg = CollectorConfig(
        search_engines=source,
        max_results_per_engine=max_results,
        depth=depth,
        language=language,
        concurrency=concurrency,
        timeout_sec=timeout,
        search_rounds=rounds,
        llm_query_expansion=llm_expand,
        extra_queries=list(query),
        official_urls=list(official_url),
        core_keyword=core,
        facets=_split_csv(facets),
        from_year=from_year,
        to_year=to_year,
        deep_search=deep_search,
        deep_pages=deep_pages,
        deep_sorts=_split_csv(deep_sorts) or ["relevance", "date", "citations"],
        search_relevance_min_overlap=search_relevance_min_overlap,
        x_backend=x_backend,
        x_cmd=x_cmd,
    )
    topic_dir = output / slugify(topic)

    async def _go():
        llm = await _make_healthy_llm() if llm_expand else None
        c = Collector(cfg, llm)
        if dry_run:
            if cfg.official_urls:
                for url in cfg.official_urls:
                    logger.info("[official] 待验证并抓取\n  %s", url)
                logger.info(
                    "dry-run 不抓取官方源，因此不执行 OpenAlex/Crossref/arXiv 扩展检索"
                )
                return
            sr = await c.search_only(topic)
            for h in sr.hits:
                logger.info("[%s] %s\n  %s", h.source_engine, h.title, h.url)
            logger.info("共 %d 条结果（dry-run，未抓取）", len(sr.hits))
            _print_warnings(sr.warnings)
            return
        res = await c.run(topic, topic_dir)
        logger.info("采集完成：%d 个文件 → %s", len(res.files), res.raw_dir)
        _print_source_audits(res.source_audits)
        _print_warnings(res.warnings)

    _run(_go())


# --------------------------------------------------------------------------- #
# ingest-pdf（pdf2zh / MinerU 集成：本地 PDF → raw/）
# --------------------------------------------------------------------------- #
@app.command(name="ingest-pdf")
def ingest_pdf(
    pdf_path: Path = typer.Argument(..., help="PDF 文件或文件夹"),
    topic: str = typer.Option(..., "-T", "--topic", help="调研主题（决定输出子目录）"),
    output: Path = typer.Option(Path("./research-output"), "-o", "--output"),
    backend: Optional[str] = typer.Option(
        None,
        "-b",
        "--backend",
        help="MinerU 后端（默认取 config.yaml 的 pdf_ingest.mineru_backend）",
    ),
    ocr_engine: str = typer.Option(
        "mineru",
        "--ocr-engine",
        help="OCR 引擎：auto/mineru/custom/paddleocr-vl/unlimited-ocr/vision-llm",
    ),
    lang: str = typer.Option("en", "-l", "--lang", help="OCR 语言提示"),
    translate: bool = typer.Option(False, "--translate", help="把英文 MD 译成中文"),
    mineru_cmd: Optional[str] = typer.Option(None, "--mineru-cmd", help="mineru 可执行路径"),
    ocr_cmd: Optional[str] = typer.Option(
        None, "--ocr-cmd", help="自定义 OCR 命令，可用 {pdf}/{out}/{lang}/{model} 占位"
    ),
    ocr_model_path: Optional[str] = typer.Option(
        None, "--ocr-model-path", help="本地 OCR 模型目录"
    ),
    model: Optional[str] = typer.Option(None, "--model", help="翻译用 LLM 模型"),
) -> None:
    """用 MinerU 把本地 PDF 转为 raw/ Markdown（可选翻译），供后续阶段接力。"""
    from ..infrastructure.ingest import PdfIngestor

    # 读 config.yaml 的 pdf_ingest 作默认，CLI 参数覆盖，确保 backend/cmd 生效。
    try:
        _base_pdf = load_config(_state.get("config_path", "config.yaml")).pdf_ingest
    except Exception:
        _base_pdf = PdfIngestConfig()
    cfg = PdfIngestConfig(
        ocr_engine=ocr_engine,
        mineru_backend=backend if backend is not None else _base_pdf.mineru_backend,
        ocr_lang=lang,
        translate=translate,
        mineru_cmd=mineru_cmd or _base_pdf.mineru_cmd,
        ocr_cmd=ocr_cmd or _base_pdf.ocr_cmd,
        ocr_model_path=ocr_model_path or _base_pdf.ocr_model_path,
    )
    topic_dir = output / slugify(topic)

    async def _go():
        llm = await _make_healthy_llm(model) if translate else None
        res = await PdfIngestor(cfg, llm).run(pdf_path, topic_dir)
        logger.info("PDF 摄取完成：%d 个文件 → %s", len(res.files), res.raw_dir)
        if translate:
            logger.info("（已翻译为中文）")

    _run(_go())


@app.command(name="ocr-engines")
def ocr_engines(
    ocr_cmd: Optional[str] = typer.Option(
        None, "--ocr-cmd", help="用于扫描 custom/model OCR 的命令"
    ),
    ocr_model_path: Optional[str] = typer.Option(
        None, "--ocr-model-path", help="本地 OCR 模型目录"
    ),
) -> None:
    """扫描当前可用 OCR 引擎。"""
    from ..infrastructure.ingest.ocr import scan_ocr_engines

    cfg = PdfIngestConfig(ocr_cmd=ocr_cmd, ocr_model_path=ocr_model_path)
    table = Table(title="OCR engines")
    table.add_column("engine")
    table.add_column("available")
    table.add_column("detail")
    for status in scan_ocr_engines(cfg):
        table.add_row(status.name, "yes" if status.available else "no", status.detail)
    _out.print(table)


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #
@app.command()
def clean(
    input_dir: Path = typer.Argument(..., help="raw/ 目录"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    no_strip_html: bool = typer.Option(False, "--no-strip-html"),
    no_strip_nav: bool = typer.Option(False, "--no-strip-nav"),
    no_strip_ads: bool = typer.Option(False, "--no-strip-ads"),
    min_length: int = typer.Option(200, "--min-length"),
) -> None:
    """阶段2：清洗去噪。"""
    _assert_safe_output_dir(output)
    cfg = CleanerConfig(
        strip_html=not no_strip_html,
        strip_nav=not no_strip_nav,
        strip_ads=not no_strip_ads,
        min_content_length=min_length,
    )
    work_dir = output.parent if output else None
    res = Cleaner(cfg).process(input_dir, work_dir)
    logger.info("清洗完成：%d 个文件 → %s", len(res.files), res.clean_dir)


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
@app.command()
def extract(
    input_dir: Path = typer.Argument(..., help="clean/ 目录"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    tasks: list[str] = typer.Option(["ner", "triple"], "--tasks"),
    entity_types: Optional[str] = typer.Option(None, "--entity-types"),
    relation_types: Optional[str] = typer.Option(None, "--relation-types"),
    chunk_size: int = typer.Option(4000, "--chunk-size"),
    overlap: int = typer.Option(200, "--overlap"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """阶段3：LLM 抽取实体/关系/三元组。"""
    _assert_safe_output_dir(output)
    cfg = ExtractorConfig(
        tasks=tasks,
        entity_types=entity_types.split(",") if entity_types else None,
        relation_types=relation_types.split(",") if relation_types else None,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    work_dir = output.parent if output else None

    async def _go():
        llm = await _make_healthy_llm(model)
        res = await Extractor(cfg).run(input_dir, llm, work_dir)
        logger.info(
            "抽取完成：%d 实体 / %d 关系 / %d 三元组 → %s",
            len(res.entities),
            len(res.relations),
            len(res.triples),
            res.output_dir,
        )

    _run(_go())


# --------------------------------------------------------------------------- #
# organize
# --------------------------------------------------------------------------- #
@app.command()
def organize(
    extracted_dir: Path = typer.Argument(..., help="extracted/ 或 clean/ 目录"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    max_nodes: int = typer.Option(7, "--max-nodes"),
    min_nodes: int = typer.Option(4, "--min-nodes"),
    topic: Optional[str] = typer.Option(None, "--topic", help="主题（默认取目录名）"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """阶段4：构建知识树。"""
    _assert_safe_output_dir(output)
    cfg = OrganizerConfig(max_nodes=max_nodes, min_nodes=min_nodes)
    work_dir = output.parent if output else None
    topic_hint = topic or input_topic_from_dir(extracted_dir)

    async def _go():
        llm = await _make_healthy_llm(model)
        res = await Organizer(cfg).run(extracted_dir, llm, work_dir, topic=topic_hint)
        logger.info("组织完成：%d 个节点 → %s", len(res.nodes), res.tree_dir)

    _run(_go())


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
@app.command()
def report(
    tree_dir: Path = typer.Argument(..., help="tree/ 目录"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    format: str = typer.Option("markdown", "--format"),
    style: str = typer.Option("report", "--style"),
    topic: Optional[str] = typer.Option(None, "--topic", help="主题（默认取目录名）"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """阶段5：合成调研报告。"""
    _assert_safe_output_dir(output)
    cfg = ReporterConfig(format=format, style=style)
    topic_hint = topic or input_topic_from_dir(tree_dir)

    async def _go():
        llm = await _make_healthy_llm(model)
        res = await Reporter(cfg).run(
            tree_dir, llm, topic=topic_hint, output_path=output
        )
        logger.info("报告完成：%d 字 → %s", res.word_count, res.report_path)

    _run(_go())


# --------------------------------------------------------------------------- #
# V1.1 VideoIngest：--video-url 视频入口（扩展 run 命令）
# --------------------------------------------------------------------------- #


def _validate_video_urls(urls: list[str]) -> list[str]:
    """校验 --video-url 列表的白名单（bilibili / youtube）；非法 URL 立即拒绝。

    Raises:
        typer.BadParameter: 含非法 URL
    """
    from ..application.video_pipeline import validate_video_url  # 延迟 import

    cleaned: list[str] = []
    for u in urls:
        if not u or not u.strip():
            continue
        u = u.strip()
        try:
            vu = validate_video_url(u)
            if vu is None:
                raise typer.BadParameter(f"不支持的 URL（仅 bilibili / youtube 一期 P0）: {u[:60]}")
            cleaned.append(u)
        except VideoIngestError as e:
            raise typer.BadParameter(f"URL 校验失败: {u[:60]}\n  {e}") from e
    if not cleaned:
        raise typer.BadParameter("至少需要 1 个有效 --video-url")
    if len(cleaned) > 10:
        raise typer.BadParameter(f"--video-url 数量 {len(cleaned)} 超过上限 10")
    return cleaned


def _run_video_ingest(
    *,
    topic: str,
    video_urls: list[str],
    output: Optional[Path],
    no_cache: bool,
    model: Optional[str],
) -> None:
    """V1.1 VideoIngest 入口（CLI 薄封装）。"""
    from ..application.video_pipeline import process_videos
    from ..common.slug import slugify

    valid_urls = _validate_video_urls(video_urls)
    work_dir = output or Path("./research-output")
    topic_dir = work_dir / slugify(topic)
    logger.info(
        "VideoIngest 启动: topic=%r, urls=%d, work_dir=%s, no_cache=%s",
        topic,
        len(valid_urls),
        topic_dir,
        no_cache,
    )

    async def _go():
        report = await process_videos(
            topic=topic,
            urls=valid_urls,
            work_dir=topic_dir,
            run_pipeline=True,  # V1.1: 视频落 raw/ 后自动触发 5 阶段管道
            use_cache=not no_cache,  # --no-cache → 跳过转写缓存强制重转
        )
        # 报告汇总
        logger.info(
            "VideoIngest 完成: 成功 %d / 失败 %d（总耗时 %.1fs）",
            report.success_count,
            report.failed_count,
            report.total_duration_ms / 1000,
        )
        for r in report.results:
            if r.status == "success":
                logger.info("  ✓ %s → %s", r.url[:60], r.markdown_path)
            else:
                logger.error("  ✗ %s — %s", r.url[:60], r.error)
        if report.stages_result is not None:
            sr = report.stages_result
            if sr.success:
                logger.info(
                    "✓ 5 阶段管道完成: %s (%.1fs)",
                    ",".join(sr.stages_run),
                    sr.duration_ms / 1000,
                )
            else:
                logger.warning(
                    "5 阶段管道部分失败: stages=%s err=%s",
                    sr.stages_run,
                    sr.error,
                )
        # 全部失败 → 退出码非 0
        if report.success_count == 0 and report.failed_count > 0:
            raise VideoIngestError(ErrorCode.E_VID_003_PIPELINE_FAIL.value, "所有视频 URL 处理失败")

    _run(_go())


def _build_run_overrides(  # noqa: PLR0915
    *,
    topic: str,
    mode: str,
    output: Path | None,
    stages: list[str],
    resume: bool,
    source: list[str],
    max_results: int,
    rounds: int | None,
    llm_expand: bool,
    query: list[str],
    core: str | None,
    facets: str | None,
    from_year: int | None,
    to_year: int | None,
    deep_search: bool | None,
    deep_pages: int | None,
    deep_sorts: str | None,
    search_relevance_min_overlap: float | None,
    x_backend: str | None,
    x_cmd: str | None,
    relevance_filter: bool,
    profile_iterations: int | None,
    max_backward_rounds: int | None,
    pdf_dir: Path | None,
    mineru_cmd: str | None,
    ocr_engine: str | None,
    ocr_cmd: str | None,
    ocr_model_path: str | None,
    translate: bool,
    model: str | None,
    with_talks: bool = False,
    max_talks: int | None = None,
    min_talk_similarity: float | None = None,
    ingest_talks: bool = False,
    experts_file: str | None = None,
    extra_url: list[str] | None = None,
    official_url: list[str] | None = None,
) -> dict:
    """从 CLI 参数构建 config overrides 字典（抽离 run 命令的超长参数）。"""
    overrides: dict = {
        "topic": topic,
        "mode": mode,
        "work_dir": str(output) if output is not None else None,
        "stages": stages,
        "resume": resume,
        "collector": {
            "max_results_per_engine": max_results,
            "llm_query_expansion": llm_expand,
            "extra_queries": list(query),
            "core_keyword": core,
            "from_year": from_year,
            "to_year": to_year,
        },
    }
    if source:
        overrides["collector"]["search_engines"] = source
    if mode == "full":
        overrides["deepen"] = {"enabled": True}
        overrides["extractor"] = {"enabled": True, "fail_on_chunk_error": True}
    if rounds is not None:
        overrides["collector"]["search_rounds"] = rounds
    if facets:
        overrides["collector"]["facets"] = _split_csv(facets)
    if deep_search is not None:
        overrides["collector"]["deep_search"] = deep_search
    if deep_pages is not None:
        overrides["collector"]["deep_pages"] = deep_pages
    if deep_sorts:
        overrides["collector"]["deep_sorts"] = _split_csv(deep_sorts)
    if search_relevance_min_overlap is not None:
        overrides["collector"]["search_relevance_min_overlap"] = search_relevance_min_overlap
    if x_backend:
        overrides["collector"]["x_backend"] = x_backend
    if x_cmd:
        overrides["collector"]["x_cmd"] = x_cmd
    if relevance_filter:
        overrides["cleaner"] = {"relevance_filter": True}
    if profile_iterations is not None:
        overrides.setdefault("deepen", {})["profile_iterations"] = profile_iterations
    if max_backward_rounds is not None:
        overrides["max_backward_rounds"] = max_backward_rounds
    if mineru_cmd:
        overrides["collector"]["mineru_cmd"] = mineru_cmd
    if experts_file:
        overrides["collector"]["experts_file"] = experts_file
    if extra_url:
        overrides["collector"]["extra_urls"] = list(extra_url)
    if official_url:
        overrides["collector"]["official_urls"] = list(official_url)
    if pdf_dir:
        overrides["pdf_dir"] = str(pdf_dir)
        overrides["pdf_ingest"] = {"translate": translate}
        if ocr_engine:
            overrides["pdf_ingest"]["ocr_engine"] = ocr_engine
        if ocr_cmd:
            overrides["pdf_ingest"]["ocr_cmd"] = ocr_cmd
        if ocr_model_path:
            overrides["pdf_ingest"]["ocr_model_path"] = ocr_model_path
        if mineru_cmd:
            overrides["pdf_ingest"]["mineru_cmd"] = mineru_cmd
    if model:
        overrides["llm"] = {"model": model}
    if with_talks or max_talks is not None or min_talk_similarity is not None or ingest_talks:
        talk: dict = {}
        if with_talks:
            talk["enabled"] = True
        if max_talks is not None:
            talk["max_talks"] = max_talks
        if min_talk_similarity is not None:
            talk["min_title_similarity"] = min_talk_similarity
        if ingest_talks:
            talk["ingest"] = True
        overrides["talk"] = talk
    return overrides


@app.command()
def run(
    topic: str = typer.Argument(..., help="调研主题"),
    # --- 常用 5 项（问题 5：run 主面板只留最常用，调优归 config.yaml）--- #
    source: list[str] = typer.Option(
        [],
        "-s",
        "--source",
        help="搜索来源（可多次；不传则使用 config.yaml，默认 Tavily 建议显式配置）",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "-o",
        "--output",
        help="输出目录（不传则取 config.yaml 的 pipeline.work_dir，默认 ./research-output）",
    ),
    skip: list[str] = typer.Option([], "--skip", help="跳过阶段，如 --skip deepen"),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="输出版本：brief（简略）| full（强制全流程）；兼容 fast/standard/deep",
    ),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="跳过已完成阶段"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅打印将执行的步骤"),
    # --- 高级项：低频，等价配置项见 config.yaml ---------------------- #
    max_results: int = typer.Option(
        8,
        "-n",
        "--max-results",
        rich_help_panel=_ADVANCED,
        help="每源最多结果（=collector.max_results_per_engine）",
    ),
    rounds: Optional[int] = typer.Option(
        None,
        "-r",
        "--rounds",
        rich_help_panel=_ADVANCED,
        help="搜索轮次 1-3（=collector.search_rounds）",
    ),
    llm_expand: bool = typer.Option(
        False,
        "--llm-expand",
        rich_help_panel=_ADVANCED,
        help="用 LLM 生成多轮查询（=collector.llm_query_expansion）",
    ),
    query: list[str] = typer.Option(
        [], "-q", "--query", rich_help_panel=_ADVANCED, help="额外查询（点名要找的论文/方法）"
    ),
    core: Optional[str] = typer.Option(
        None, "--core", rich_help_panel=_ADVANCED, help="核心词去锚（=collector.core_keyword）"
    ),
    facets: Optional[str] = typer.Option(
        None, "--facets", rich_help_panel=_ADVANCED, help="维度标签逗号分隔（=collector.facets）"
    ),
    from_year: Optional[int] = typer.Option(
        None, "--from-year", rich_help_panel=_ADVANCED, help="发表年份下限（=collector.from_year）"
    ),
    to_year: Optional[int] = typer.Option(
        None, "--to-year", rich_help_panel=_ADVANCED, help="发表年份上限（=collector.to_year）"
    ),
    deep_search: Optional[bool] = typer.Option(
        None,
        "--deep-search/--no-deep-search",
        rich_help_panel=_ADVANCED,
        help="深搜：多排序×多页翻页（=collector.deep_search）",
    ),
    deep_pages: Optional[int] = typer.Option(
        None,
        "--deep-pages",
        rich_help_panel=_ADVANCED,
        help="深搜页数（=collector.deep_pages，默认 3）",
    ),
    deep_sorts: Optional[str] = typer.Option(
        None,
        "--deep-sorts",
        rich_help_panel=_ADVANCED,
        help="深搜排序逗号分隔（=collector.deep_sorts，默认 relevance,date,citations）",
    ),
    search_relevance_min_overlap: Optional[float] = typer.Option(
        None,
        "--search-relevance-min-overlap",
        rich_help_panel=_ADVANCED,
        help="抓取前轻量相关性阈值，0=关闭（=collector.search_relevance_min_overlap）",
    ),
    x_backend: Optional[str] = typer.Option(
        None,
        "--x-backend",
        rich_help_panel=_ADVANCED,
        help="X/Twitter 后端：opencli 或 twitter-cli（=collector.x_backend）",
    ),
    x_cmd: Optional[str] = typer.Option(
        None,
        "--x-cmd",
        rich_help_panel=_ADVANCED,
        help="twitter-cli 命令名或路径（=collector.x_cmd）",
    ),
    relevance_filter: bool = typer.Option(
        False,
        "--relevance-filter",
        rich_help_panel=_ADVANCED,
        help="LLM 按主题给清洗后文档评 0-1 分，剔低分（=cleaner.relevance_filter）",
    ),
    profile_iterations: Optional[int] = typer.Option(
        None,
        "--profile-iterations",
        rich_help_panel=_ADVANCED,
        help="画像迭代轮数 1-5，≥2 启用时间线回溯+同名消歧（=deepen.profile_iterations）",
    ),
    max_backward_rounds: Optional[int] = typer.Option(
        None,
        "--max-backward-rounds",
        rich_help_panel=_ADVANCED,
        help="反向传播轮数 0-3，>0 启用知识树质量评估循环（=pipeline.max_backward_rounds）",
    ),
    pdf_dir: Optional[Path] = typer.Option(
        None, "--pdf-dir", rich_help_panel=_ADVANCED, help="改用本地 PDF 文件夹作为数据源"
    ),
    mineru_cmd: Optional[str] = typer.Option(
        None,
        "--mineru-cmd",
        rich_help_panel=_ADVANCED,
        help="mineru 路径（Web 抓到的 PDF 也用它解析）",
    ),
    ocr_engine: Optional[str] = typer.Option(
        None,
        "--ocr-engine",
        rich_help_panel=_ADVANCED,
        help="PDF OCR 引擎：auto/mineru/custom/paddleocr-vl/unlimited-ocr/vision-llm",
    ),
    ocr_cmd: Optional[str] = typer.Option(
        None,
        "--ocr-cmd",
        rich_help_panel=_ADVANCED,
        help="PDF OCR 命令，可用 {pdf}/{out}/{lang}/{model} 占位",
    ),
    ocr_model_path: Optional[str] = typer.Option(
        None, "--ocr-model-path", rich_help_panel=_ADVANCED, help="本地 OCR 模型目录"
    ),
    translate: bool = typer.Option(
        False, "--translate", rich_help_panel=_ADVANCED, help="PDF 英文 MD 译成中文"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", rich_help_panel=_ADVANCED, help="覆盖 LLM 模型（=llm.model）"
    ),
    video_url: list[str] = typer.Option(
        [],
        "--video-url",
        rich_help_panel=_ADVANCED,
        help=(
            "V1.1 VideoIngest：视频 URL（可多次）。一期 P0 仅支持 bilibili.com / b23.tv / "
            "youtube.com / youtu.be。多个 URL 默认 3 并发。"
        ),
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        rich_help_panel=_ADVANCED,
        help="V1.1 VideoIngest：跳过 M-004 转写缓存（强制重转）。",
    ),
    with_talks: bool = typer.Option(
        False,
        "--with-talks",
        rich_help_panel=_ADVANCED,
        help="阶段 F：organize 后把论文标题关联 YouTube talk（=talk.enabled）",
    ),
    max_talks: Optional[int] = typer.Option(
        None,
        "--max-talks",
        rich_help_panel=_ADVANCED,
        help="talk 关联硬上限（=talk.max_talks，默认 5）",
    ),
    min_talk_similarity: Optional[float] = typer.Option(
        None,
        "--min-talk-similarity",
        rich_help_panel=_ADVANCED,
        help="talk 标题相似度置信闸 0-1（=talk.min_title_similarity，默认 0.7）",
    ),
    ingest_talks: bool = typer.Option(
        False,
        "--ingest-talks",
        rich_help_panel=_ADVANCED,
        help="talk 命中后全量 VideoIngest 转写（=talk.ingest；默认仅 discovery 笔记）",
    ),
    experts_file: Optional[str] = typer.Option(
        None,
        "--experts-file",
        rich_help_panel=_ADVANCED,
        help="专家库 YAML 路径（=collector.experts_file）",
    ),
    extra_url: list[str] = typer.Option(
        [],
        "--extra-url",
        rich_help_panel=_ADVANCED,
        help="直塞抓取队列的 URL（可多次，=collector.extra_urls）",
    ),
    official_url: list[str] = typer.Option(
        [],
        "--official-url",
        help="新论文官方来源；先强制抓取，再自动扩展 OpenAlex/Crossref/arXiv",
    ),
) -> None:
    """一键全流程：collect/PDF摄取 → clean → extract → organize → report。

    V1.1 扩展：传 --video-url 时进入 VideoIngest 流程（下载→转写→总结→raw/）。
    """
    # V1.1 VideoIngest 入口：--video-url 优先于其他 stage
    _assert_safe_output_dir(output)
    if video_url:
        _run_video_ingest(
            topic=topic,
            video_urls=video_url,
            output=output,
            no_cache=no_cache,
            model=model,
        )
        return

    configured = load_config(_state["config_path"])
    _assert_safe_output_dir(output, configured.work_dir)
    effective_mode = mode or configured.mode
    if effective_mode not in {"brief", "full", "fast", "standard", "deep"}:
        _fail(
            f"未知调研模式: {effective_mode}（推荐 brief/full；兼容 fast/standard/deep）"
        )
    is_deepen_strategy = bool(
        configured.nine_loop.enabled and configured.nine_loop.deepen_as_strategy
    )
    all_stages = ["collect", "deepen", "clean", "extract", "organize", "report"]
    if mode is None:
        mode_stages = list(configured.stages)
    elif effective_mode == "brief":
        if os.environ.get("RESEARCH_AGENT_STRICT", "0") == "1":
            _fail("安全拦截：当前环境开启了 RESEARCH_AGENT_STRICT，严禁使用 --mode brief 偷懒缩水，必须执行完整管线！")
        logger.warning(
            "⚠️ 警告：当前以 --mode brief 运行，仅执行 collect → clean → report（跳过抽取与知识树构建）。"
            "正式深度调研请使用默认九段管线或 --mode full。"
        )
        mode_stages = ["collect", "clean", "report"]
    elif effective_mode == "full" and is_deepen_strategy:
        mode_stages = ["collect", "clean", "extract", "organize", "report"]
    else:
        mode_stages = [
            stage
            for stage in all_stages
            if not (effective_mode == "fast" and stage == "deepen")
        ]
    if effective_mode == "full" and skip:
        _fail(
            f"--mode full 强制执行{'五' if is_deepen_strategy else '六'}阶段，不能同时使用 --skip"
        )
    stages = [s for s in mode_stages if s not in skip]

    # P0-B 闭环：统一在最终 stages 集合上执行 Agent Strict 审查，杜绝任何形式的绕过
    is_strict_agent = os.environ.get("RESEARCH_AGENT_STRICT", "0") == "1"
    if is_strict_agent:
        required_stages = {"collect", "clean", "extract", "organize", "report"}
        missing = required_stages - set(stages)
        if missing:
            _fail(
                f"安全拦截（RESEARCH_AGENT_STRICT）：严禁缩水或跳过核心阶段！"
                f"缺失核心阶段：{sorted(missing)}。Agent 调研必须执行完整管线（collect → clean → extract → organize → report）。"
            )

    if dry_run:
        logger.info("mode=%s；将执行的阶段：%s", effective_mode, " → ".join(stages))
        raise typer.Exit()

    overrides = _build_run_overrides(
        topic=topic,
        mode=effective_mode,
        output=output,
        stages=stages,
        resume=resume,
        source=source,
        max_results=max_results,
        rounds=rounds,
        llm_expand=llm_expand,
        query=query,
        core=core,
        facets=facets,
        from_year=from_year,
        to_year=to_year,
        deep_search=deep_search,
        deep_pages=deep_pages,
        deep_sorts=deep_sorts,
        search_relevance_min_overlap=search_relevance_min_overlap,
        x_backend=x_backend,
        x_cmd=x_cmd,
        relevance_filter=relevance_filter,
        profile_iterations=profile_iterations,
        max_backward_rounds=max_backward_rounds,
        pdf_dir=pdf_dir,
        mineru_cmd=mineru_cmd,
        ocr_engine=ocr_engine,
        ocr_cmd=ocr_cmd,
        ocr_model_path=ocr_model_path,
        translate=translate,
        model=model,
        with_talks=with_talks,
        max_talks=max_talks,
        min_talk_similarity=min_talk_similarity,
        ingest_talks=ingest_talks,
        experts_file=experts_file,
        extra_url=extra_url,
        official_url=official_url,
    )

    async def _go():
        cfg = load_config(_state["config_path"], overrides=overrides)
        pipeline = ResearchPipeline(cfg)
        async for ev in pipeline.stream(topic):
            logger.info("%-9s %-9s %s", ev.status, ev.stage, ev.message)
        res = pipeline._result
        if res and res.collect_result:
            _print_source_audits(res.collect_result.source_audits)
            _print_warnings(res.collect_result.warnings)
        if res and res.deepen_result:
            _print_warnings(res.deepen_result.warnings)
        if res and res.failed_stage:
            _fail(f"在 {res.failed_stage} 阶段失败")
        if res and res.report_result:
            logger.info("✓ 报告：%s", res.report_result.report_path)
        if res:
            logger.info("耗时 %.1fs", res.elapsed_sec)
            logger.info(
                "运行档位=%s resume=%s search_cache=%s",
                cfg.mode,
                cfg.resume,
                cfg.collector.search_cache,
            )
            if res.run_summary_path:
                logger.info("运行摘要：%s", res.run_summary_path)

    _run(_go())


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
@app.command(name="wiki-stage")
def wiki_stage(
    source: Path = typer.Argument(..., help="单个 research-output 主题目录"),
    package_output: Path = typer.Option(
        Path("./research-packages"),
        "--package-output",
        help="Vault 外、受 draft importer 允许的内容寻址研究包目录（默认 ./research-packages）",
    ),
    build: bool = typer.Option(False, "--build", help="原子生成不可变包；默认仅预览"),
) -> None:
    """生成仅供未验证 Wiki 草稿入口使用的不可变研究包。"""
    try:
        if not build:
            typer.echo(json.dumps(plan_stage_package(source, package_output), ensure_ascii=False))
            return
        result = build_stage_package(source, package_output)
    except PackageStageError as exc:
        _fail(str(exc))
        return
    typer.echo(json.dumps({
        "status": "success",
        "packageId": result.package_id,
        "packageHash": result.package_hash,
        "packagePath": str(result.package_path),
        "verification": "unverified",
        "target": "isolated_research_draft",
        "promotion": "forbidden",
        "alreadyExists": result.already_exists,
    }, ensure_ascii=False))


@app.command(name="publish-wiki")
def publish_wiki(
    slug: str = typer.Argument(..., help="research-output 下的主题目录名"),
    vault: Path = typer.Option(..., "--vault", help="Obsidian staging vault 路径"),
    research_output: Path = typer.Option(
        Path("./research-output"), "--research-output", help="research-output 根目录"
    ),
    confirm: bool = typer.Option(False, "--confirm", help="将产物标为 verified（默认 draft）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示将写入的文件"),
) -> None:
    """将一份研究产物发布到受控的 Obsidian staging vault。"""
    try:
        result = publish_to_wiki(
            slug, vault, research_output=research_output, confirm=confirm, dry_run=dry_run
        )
    except ValueError as exc:
        _fail(str(exc))
        return
    action = "将写入" if result.dry_run else "已写入"
    logger.info("%s %d 个目标：%s", action, len(result.written), result.form.value)
    for path in result.written:
        logger.info("  %s", path)
    _print_warnings(list(result.warnings))


@app.command()
def status(path: Path = typer.Argument(..., help="主题目录")) -> None:
    """查看调研进度。"""
    stages = [
        ("collect", path / "raw", "*.md"),
        ("clean", path / "clean", "*.md"),
        ("extract", path / "extracted", "*.json"),
        ("organize", path / "tree", "*.md"),
        ("report", path, "report.*"),
    ]
    table = Table(title=f"主题: {path.name}")
    table.add_column("阶段")
    table.add_column("状态")
    table.add_column("文件数")
    for i, (name, d, pat) in enumerate(stages, 1):
        files = list(d.glob(pat)) if d.exists() else []
        # report.* 不含子目录里的 md
        if name == "report":
            files = [f for f in files if f.name.startswith("report.")]
        done = len(files) > 0
        table.add_row(
            f"阶段{i} [{name}]",
            "[green]✓ 完成[/green]" if done else "[dim]✗ 未执行[/dim]",
            str(len(files)) if done else "-",
        )
    _out.print(table)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
@app.command()
def ui(
    port: int = typer.Option(7861, "--port", help="Web 界面端口"),
    no_browser: bool = typer.Option(False, "--no-browser", help="不自动打开浏览器"),
) -> None:
    """启动可视化 Web 界面（Gradio）。"""
    try:
        from ..presentation.webui import main as ui_main
    except ImportError:
        _fail('未安装 Web 界面依赖，请先：pip install "research-tool[ui]"（或 pip install gradio）')
        return
    ui_main(server_port=port, inbrowser=not no_browser)


@app.command()
def config() -> None:
    """显示当前解析后的配置。"""
    try:
        cfg = load_config(_state["config_path"])
    except ResearchToolError as e:
        _fail(str(e))
        return
    data = _redact_config_data(cfg.model_dump(mode="json"))
    _out.print_json(json.dumps(data, ensure_ascii=False))


@app.command("setup")
def setup_cmd(
    secrets_only: bool = typer.Option(
        False,
        "--secrets-only",
        "-s",
        help="逐步更新全部密钥（LLM/GitHub/Tavily/S2/代理/Groq/X…）",
    ),
    github_only: bool = typer.Option(
        False,
        "--github-only",
        "-g",
        help="只更新 GITHUB_TOKEN",
    ),
    tavily_only: bool = typer.Option(
        False,
        "--tavily-only",
        "-t",
        help="只更新 TAVILY_API_KEY",
    ),
    check_only: bool = typer.Option(
        False,
        "--check-only",
        help="按 receipt/--profile 重新验收，不安装、不提问",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="直接部署/验收档位：minimal | recommended | full",
    ),
) -> None:
    """交互式部署 / 密钥配置（密钥本地隐藏输入，勿粘贴到聊天）。"""
    import runpy
    from pathlib import Path

    # Wheel 使用随包脚本；源码运行时仅回退到已验证仓库内的 scripts/。
    candidates = [
        Path(__file__).with_name("setup_interactive.py"),
        Path(__file__).resolve().parents[2] / "scripts" / "setup_interactive.py",
    ]
    script = next((p for p in candidates if p.is_file()), None)
    if script is None:
        _fail(
            "找不到 scripts/setup_interactive.py。请在 research-tool 仓库根目录执行：\n"
            "  python scripts/setup_interactive.py --tavily-only"
        )
        return
    argv = []
    if secrets_only:
        argv.append("--secrets-only")
    if github_only:
        argv.append("--github-only")
    if tavily_only:
        argv.append("--tavily-only")
    if check_only:
        argv.append("--check-only")
    if profile:
        argv.extend(("--profile", profile))
    # 用 runpy 执行脚本的 main，避免再起子进程丢 TTY
    import sys

    old = sys.argv
    try:
        sys.argv = [str(script), *argv]
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = old


def _run(coro) -> None:
    """统一跑 async + 异常 → stderr。"""
    try:
        asyncio.run(coro)
    except VideoIngestError as e:
        _fail_video_ingest(e)
    except ResearchToolError as e:
        _fail(str(e))
    except KeyboardInterrupt:  # pragma: no cover
        _fail("已中断")


if __name__ == "__main__":
    app()
