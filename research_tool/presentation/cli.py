"""命令行接口。

依据 02-CLI接口设计.md。CLI 是薄封装，直接调用核心引擎；用户输出经 logging 模块，
规范化消息 → stdout（INFO，无时间戳），问题 → stderr（WARNING+，含时间戳+模块）。
"""

from __future__ import annotations

import asyncio
import json
import re
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
from ..domain.models import PdfIngestConfig
from ..application.pipeline import ResearchPipeline
from ..common.slug import slugify
from ..infrastructure.export.wiki_publisher import publish as publish_to_wiki
from ..infrastructure.export.wiki_stage import (
    PackageStageError,
    build_stage_package,
    plan_stage_package,
    resolve_current_output,
    validate_current_run,
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


def _redact_nested_secrets(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***" if re.search(
                r"api[_\W]*key|token|auth|password|secret|cookie|credential|private[_\W]*key",
                str(key), re.IGNORECASE,
            ) else _redact_nested_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_nested_secrets(item) for item in value]
    return value


def _redact_config_data(data: dict) -> dict:
    """Return a redacted copy safe for terminal and CI logs."""
    sanitized = _redact_nested_urls(data)
    if not isinstance(sanitized, dict):
        return {}
    llm = dict(sanitized.get("llm") or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    if "request_options" in llm:
        llm["request_options"] = _redact_nested_secrets(llm["request_options"])
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


def _make_llm(model: str | None = None) -> LLMClient:
    overrides = {"llm": {"model": model}} if model else None
    cfg = load_config(_state["config_path"], overrides=overrides)
    return LLMClient.from_config(cfg.llm)


async def _make_healthy_llm(model: str | None = None) -> LLMClient:
    llm = _make_llm(model)
    await llm.healthcheck()
    return llm


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
            work_dir=work_dir,
            run_pipeline=True,  # 视频 raw/ 经 input_dir 进入唯一九段主链
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
                    "九段管道运行结束: %s (%.1fs)",
                    ",".join(sr.stages_run),
                    sr.duration_ms / 1000,
                )
            else:
                logger.warning(
                    "九段管道未完成: stages=%s err=%s",
                    sr.stages_run,
                    sr.error,
                )
                raise VideoIngestError(
                    ErrorCode.E_VID_003_PIPELINE_FAIL.value,
                    f"九段调研未完成: {sr.error or 'unknown error'}",
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
    input_dir: Path | None,
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
    max_rounds: int | None,
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
        "stages": [
            "collect", "clean", "extract", "knowledge", "inspect",
            "targeted", "merge", "qgate", "report",
        ],
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
    if input_dir is not None:
        overrides["input_dir"] = str(input_dir)
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
    if max_rounds is not None:
        overrides["nine_loop"] = {"max_rounds": max_rounds}
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
    input_dir: Optional[Path] = typer.Option(
        None, "--input-dir", help="使用已有 raw/*.md 输入目录并跳过 collect 搜索"
    ),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="完整九段链：full / standard / deep（只改变模型预算）",
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
    max_rounds: Optional[int] = typer.Option(
        None,
        "--max-rounds",
        rich_help_panel=_ADVANCED,
        help="九段质量闭环最大轮数（=nine_loop.max_rounds）",
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
        help="在研究流程中关联论文标题与 YouTube talk（=talk.enabled）",
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
    """一键运行固定九段研究主链。

    V1.1 扩展：传 --video-url 时进入 VideoIngest 流程（下载→转写→总结→raw/）。
    """
    configured = load_config(_state["config_path"])
    effective_mode = mode or configured.mode
    if effective_mode not in {"full", "standard", "deep"}:
        _fail(f"未知调研模式: {effective_mode}（只支持 full/standard/deep，均执行完整九段链）")

    if dry_run:
        logger.info(
            "mode=%s；将执行的阶段：%s",
            effective_mode,
            " → ".join((
                "collect", "clean", "extract", "knowledge", "inspect",
                "targeted", "merge", "qgate", "report",
            )),
        )
        raise typer.Exit()

    # 视频摄入后由适配器把 raw/ 交给同一九段主链。
    if video_url:
        _run_video_ingest(
            topic=topic,
            video_urls=video_url,
            output=output,
            no_cache=no_cache,
            model=model,
        )
        return

    overrides = _build_run_overrides(
        topic=topic,
        mode=effective_mode,
        output=output,
        input_dir=input_dir,
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
        max_rounds=max_rounds,
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
        if res and res.failed_stage:
            _fail(f"在 {res.failed_stage} 阶段失败")
        expected_stages = {
            "collect", "clean", "extract", "knowledge", "inspect",
            "targeted", "merge", "qgate", "report",
        }
        if res is None or (
            res.verdict != "STOP_SUCCESS"
            or res.report_result is None
            or not expected_stages.issubset(
                set(res.stages_completed) | set(res.stages_skipped)
            )
        ):
            _fail("九段管线未完整完成")
        try:
            _summary, _runtime, final_dir = validate_current_run(res.topic_dir)
            if (final_dir is None or res.report_result.report_path.resolve()
                    != (final_dir / "report.md").resolve()):
                raise PackageStageError("运行结果与当前报告路径不一致")
        except PackageStageError as exc:
            _fail(f"九段成果校验失败：{exc}")
            return
        logger.info("✓ 报告：%s", final_dir / "report.md")
        if res:
            logger.info("耗时 %.1fs", res.elapsed_sec)
            logger.info(
                "运行档位=%s resume=%s",
                cfg.mode,
                cfg.resume,
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
        source = resolve_current_output(source)
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
        topic_dir = Path(research_output) / slug
        if (topic_dir / "run-summary.json").exists():
            raise PackageStageError(
                "publish-wiki 不支持九段版本化输出；请使用 wiki-stage 导出当前成功 final"
            )
        result = publish_to_wiki(
            slug, vault, research_output=research_output, confirm=confirm, dry_run=dry_run
        )
    except (ValueError, PackageStageError) as exc:
        _fail(str(exc))
        return
    action = "将写入" if result.dry_run else "已写入"
    logger.info("%s %d 个目标：%s", action, len(result.written), result.form.value)
    for path in result.written:
        logger.info("  %s", path)
    _print_warnings(list(result.warnings))


@app.command(name="retrieval")
def retrieval(
    action: str = typer.Argument(
        ...,
        help="ResearchCTL 动作：index/query/sources/trace/history/reconcile/generate-index",
    ),
    root: Path = typer.Option(
        Path("./research-output"), "--root", help="research-output 根目录或主题目录"
    ),
    researchctl_root: Optional[Path] = typer.Option(
        None, "--researchctl-root", help="ResearchCTL 仓库根目录"
    ),
    db: Optional[Path] = typer.Option(None, "--db", help="ResearchCTL 派生索引路径"),
    entity: Optional[str] = typer.Option(None, "--entity"),
    text: Optional[str] = typer.Option(None, "--text"),
    owner: Optional[str] = typer.Option(None, "--owner"),
    as_of: Optional[str] = typer.Option(None, "--as-of"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    semantic: bool = typer.Option(False, "--semantic"),
) -> None:
    """通过 ResearchCTL 公开 CLI 查询 research-tool 运行状态。"""
    from ..infrastructure.retrieval import ResearchCtlClient, RetrievalClientError

    client = ResearchCtlClient(
        root,
        researchctl_root=researchctl_root,
        db_path=db,
    )
    try:
        if action == "index":
            payload = client.index(semantic=semantic)
        elif action == "query":
            payload = client.query(
                entity=entity,
                text=text,
                semantic=semantic,
                limit=limit,
            )
        elif action == "sources":
            if owner is None:
                _fail("retrieval sources 需要 --owner")
                return
            payload = client.sources(owner)
        elif action == "trace":
            if entity is None:
                _fail("retrieval trace 需要 --entity")
                return
            payload = client.trace(entity)
        elif action == "history":
            payload = client.history(as_of=as_of)
        elif action == "reconcile":
            payload = client.reconcile()
        elif action == "generate-index":
            payload = client.generate_index()
        else:
            _fail(f"未知 retrieval action: {action}")
            return
    except (RetrievalClientError, ValueError) as exc:
        _fail(str(exc))
        return
    _out.print_json(json.dumps(payload, ensure_ascii=False))


@app.command()
def status(path: Path = typer.Argument(..., help="主题目录")) -> None:
    """根据九段运行摘要和 runtime-state 查看调研进度。"""
    from ..domain.models import NINE_STAGES

    stage_names = list(NINE_STAGES)
    summary: dict = {}
    state: dict = {}
    output_dir: Path | None = None
    valid_run = False
    try:
        summary, state, output_dir = validate_current_run(path)
        valid_run = True
    except PackageStageError:
        pass
    runtime_stages = {
        key.split(":", 1)[0] for key in state.get("completed_stages", {})
    }
    summary_completed = set(summary.get("stages_completed", []))
    summary_skipped = set(summary.get("stages_skipped", []))
    table = Table(title=f"主题: {path.name}")
    table.add_column("阶段")
    table.add_column("状态")
    table.add_column("运行证据")
    for i, name in enumerate(stage_names, 1):
        accounted = name in summary_completed or name in summary_skipped
        runtime_evidence = name in runtime_stages or name in summary_skipped
        done = valid_run and accounted and runtime_evidence
        table.add_row(
            f"阶段{i} [{name}]",
            "[green]✓ 完成[/green]" if done else "[dim]✗ 未执行[/dim]",
            "已记录" if done else "-",
        )
    _out.print(table)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
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
