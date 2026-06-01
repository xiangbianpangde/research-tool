"""命令行接口。

依据 02-CLI接口设计.md。CLI 是薄封装，直接调用核心引擎；用户输出经 logging 模块，
规范化消息 → stdout（INFO，无时间戳），问题 → stderr（WARNING+，含时间戳+模块）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..domain.config import load_config
from ..domain.errors import ResearchToolError
from ..infrastructure.llm.base import LLMClient
from ..common.logging_config import get_logger, setup_logging
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

logger = get_logger(__name__)

app = typer.Typer(
    add_completion=False,
    help="research —— 给定主题，产出知识树/调研报告",
    no_args_is_help=True,
)

# rich Console 仅用于渲染 JSON/Table 等结构化输出（非日志消息）
_out = Console()

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


def _split_csv(value: str | None) -> list[str]:
    """逗号分隔字符串 → 去空白去空项列表。None/空串 → []。"""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _print_warnings(warnings: list[str]) -> None:
    """搜索/深挖告警输出到 stderr。"""
    for w in warnings or []:
        logger.warning(w)


def input_topic_from_dir(path: Path) -> str:
    """从 raw/clean/extracted/tree 的父目录名推断主题（slug 形式）。"""
    p = Path(path)
    parent = p.parent if p.name in ("raw", "clean", "extracted", "tree") else p
    return parent.name.replace("-", " ")


def _make_llm(model: str | None = None) -> LLMClient:
    overrides = {"llm": {"model": model}} if model else None
    cfg = load_config(_state["config_path"], overrides=overrides)
    return LLMClient.from_config(cfg.llm)


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
    core: Optional[str] = typer.Option(
        None, "--core", help="核心词（去锚锚点），如 topic 含机构名时填人物名突破偏差"
    ),
    facets: Optional[str] = typer.Option(
        None, "--facets", help="维度标签，逗号分隔（仅去锚阶段生效），如 博士,论文,南洋理工"
    ),
    from_year: Optional[int] = typer.Option(
        None, "--from-year", help="发表年份下限（含），openalex/s2/crossref/pubmed 原生过滤"
    ),
    to_year: Optional[int] = typer.Option(
        None, "--to-year", help="发表年份上限（含）"
    ),
    deep_search: bool = typer.Option(
        False, "--deep-search", help="深搜模式：多排序×多页翻页，突破单次第1页覆盖不足"
    ),
    deep_pages: int = typer.Option(
        3, "--deep-pages", help="深搜翻页页数 1-10（仅 --deep-search 生效）"
    ),
    deep_sorts: str = typer.Option(
        "relevance,date,citations", "--deep-sorts",
        help="深搜排序策略，逗号分隔：relevance/date/citations",
    ),
    output: Path = typer.Option(Path("./research-output"), "-o", "--output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅搜索不抓取"),
) -> None:
    """阶段1：搜索并抓取原始资料。"""
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
        core_keyword=core,
        facets=_split_csv(facets),
        from_year=from_year,
        to_year=to_year,
        deep_search=deep_search,
        deep_pages=deep_pages,
        deep_sorts=_split_csv(deep_sorts) or ["relevance", "date", "citations"],
    )
    topic_dir = output / slugify(topic)

    async def _go():
        c = Collector(cfg, _make_llm() if llm_expand else None)
        if dry_run:
            sr = await c.search_only(topic)
            for h in sr.hits:
                logger.info("[%s] %s\n  %s", h.source_engine, h.title, h.url)
            logger.info("共 %d 条结果（dry-run，未抓取）", len(sr.hits))
            _print_warnings(sr.warnings)
            return
        res = await c.run(topic, topic_dir)
        logger.info("采集完成：%d 个文件 → %s", len(res.files), res.raw_dir)
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
    backend: str = typer.Option("pipeline", "-b", "--backend", help="MinerU 后端"),
    lang: str = typer.Option("en", "-l", "--lang", help="OCR 语言提示"),
    translate: bool = typer.Option(False, "--translate", help="把英文 MD 译成中文"),
    mineru_cmd: Optional[str] = typer.Option(None, "--mineru-cmd", help="mineru 可执行路径"),
    model: Optional[str] = typer.Option(None, "--model", help="翻译用 LLM 模型"),
) -> None:
    """用 MinerU 把本地 PDF 转为 raw/ Markdown（可选翻译），供后续阶段接力。"""
    from ..infrastructure.ingest import PdfIngestor

    cfg = PdfIngestConfig(
        mineru_backend=backend, ocr_lang=lang, translate=translate, mineru_cmd=mineru_cmd
    )
    topic_dir = output / slugify(topic)

    async def _go():
        llm = _make_llm(model) if translate else None
        res = await PdfIngestor(cfg, llm).run(pdf_path, topic_dir)
        logger.info("PDF 摄取完成：%d 个文件 → %s", len(res.files), res.raw_dir)
        if translate:
            logger.info("（已翻译为中文）")

    _run(_go())


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
    cfg = ExtractorConfig(
        tasks=tasks,
        entity_types=entity_types.split(",") if entity_types else None,
        relation_types=relation_types.split(",") if relation_types else None,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    work_dir = output.parent if output else None

    async def _go():
        res = await Extractor(cfg).run(input_dir, _make_llm(model), work_dir)
        logger.info(
            "抽取完成：%d 实体 / %d 关系 / %d 三元组 → %s",
            len(res.entities), len(res.relations), len(res.triples), res.output_dir,
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
    cfg = OrganizerConfig(max_nodes=max_nodes, min_nodes=min_nodes)
    work_dir = output.parent if output else None
    topic_hint = topic or input_topic_from_dir(extracted_dir)

    async def _go():
        res = await Organizer(cfg).run(
            extracted_dir, _make_llm(model), work_dir, topic=topic_hint
        )
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
    cfg = ReporterConfig(format=format, style=style)
    topic_hint = topic or input_topic_from_dir(tree_dir)

    async def _go():
        res = await Reporter(cfg).run(
            tree_dir, _make_llm(model), topic=topic_hint, output_path=output
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
    from ..domain.errors import VideoIngestError

    cleaned: list[str] = []
    for u in urls:
        if not u or not u.strip():
            continue
        u = u.strip()
        try:
            vu = validate_video_url(u)
            if vu is None:
                raise typer.BadParameter(
                    f"不支持的 URL（仅 bilibili / youtube 一期 P0）: {u[:60]}"
                )
            cleaned.append(u)
        except VideoIngestError as e:
            raise typer.BadParameter(
                f"URL 校验失败: {u[:60]}\n  {e}"
            ) from e
    if not cleaned:
        raise typer.BadParameter("至少需要 1 个有效 --video-url")
    if len(cleaned) > 10:
        raise typer.BadParameter(
            f"--video-url 数量 {len(cleaned)} 超过上限 10"
        )
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
    from ..domain.errors import VideoIngestError

    valid_urls = _validate_video_urls(video_urls)
    work_dir = output or Path("./research-output")
    topic_dir = work_dir / slugify(topic)
    logger.info(
        "VideoIngest 启动: topic=%r, urls=%d, work_dir=%s, no_cache=%s",
        topic, len(valid_urls), topic_dir, no_cache,
    )

    async def _go():
        report = await process_videos(
            topic=topic,
            urls=valid_urls,
            work_dir=topic_dir,
            run_pipeline=True,  # V1.1: 视频落 raw/ 后自动触发 5 阶段管道
        )
        # 报告汇总
        logger.info(
            "VideoIngest 完成: 成功 %d / 失败 %d（总耗时 %.1fs）",
            report.success_count, report.failed_count, report.total_duration_ms / 1000,
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
                    ",".join(sr.stages_run), sr.duration_ms / 1000,
                )
            else:
                logger.warning(
                    "5 阶段管道部分失败: stages=%s err=%s",
                    sr.stages_run, sr.error,
                )
        # 全部失败 → 退出码非 0
        if report.success_count == 0 and report.failed_count > 0:
            raise VideoIngestError("E_VID_PIPELINE_FAIL", "所有视频 URL 处理失败")

    _run(_go())




def _build_run_overrides(
    *,
    topic: str,
    output: Path | None,
    stages: list[str],
    resume: bool,
    source: list[str],
    max_results: int,
    rounds: int,
    llm_expand: bool,
    query: list[str],
    core: str | None,
    facets: str | None,
    from_year: int | None,
    to_year: int | None,
    deep_search: bool,
    deep_pages: int | None,
    deep_sorts: str | None,
    relevance_filter: bool,
    profile_iterations: int | None,
    max_backward_rounds: int | None,
    pdf_dir: Path | None,
    mineru_cmd: str | None,
    translate: bool,
    model: str | None,
) -> dict:
    """从 CLI 参数构建 config overrides 字典（抽离 run 命令的超长参数）。"""
    overrides: dict = {
        "topic": topic,
        "work_dir": str(output) if output is not None else None,
        "stages": stages,
        "resume": resume,
        "collector": {
            "search_engines": source,
            "max_results_per_engine": max_results,
            "search_rounds": rounds,
            "llm_query_expansion": llm_expand,
            "extra_queries": list(query),
            "core_keyword": core,
            "from_year": from_year,
            "to_year": to_year,
        },
    }
    if facets:
        overrides["collector"]["facets"] = _split_csv(facets)
    if deep_search:
        overrides["collector"]["deep_search"] = True
    if deep_pages is not None:
        overrides["collector"]["deep_pages"] = deep_pages
    if deep_sorts:
        overrides["collector"]["deep_sorts"] = _split_csv(deep_sorts)
    if relevance_filter:
        overrides["cleaner"] = {"relevance_filter": True}
    if profile_iterations is not None:
        overrides["deepen"] = {"profile_iterations": profile_iterations}
    if max_backward_rounds is not None:
        overrides["max_backward_rounds"] = max_backward_rounds
    if mineru_cmd:
        overrides["collector"]["mineru_cmd"] = mineru_cmd
    if pdf_dir:
        overrides["pdf_dir"] = str(pdf_dir)
        overrides["pdf_ingest"] = {"translate": translate}
        if mineru_cmd:
            overrides["pdf_ingest"]["mineru_cmd"] = mineru_cmd
    if model:
        overrides["llm"] = {"model": model}
    return overrides


@app.command()
def run(
    topic: str = typer.Argument(..., help="调研主题"),
    # --- 常用 5 项（问题 5：run 主面板只留最常用，调优归 config.yaml）--- #
    source: list[str] = typer.Option(["web"], "-s", "--source", help="搜索来源（可多次）"),
    output: Optional[Path] = typer.Option(
        None, "-o", "--output",
        help="输出目录（不传则取 config.yaml 的 pipeline.work_dir，默认 ./research-output）",
    ),
    skip: list[str] = typer.Option([], "--skip", help="跳过阶段，如 --skip deepen"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="跳过已完成阶段"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅打印将执行的步骤"),
    # --- 高级项：低频，等价配置项见 config.yaml ---------------------- #
    max_results: int = typer.Option(
        8, "-n", "--max-results", rich_help_panel=_ADVANCED, help="每源最多结果（=collector.max_results_per_engine）"
    ),
    rounds: int = typer.Option(
        1, "-r", "--rounds", rich_help_panel=_ADVANCED, help="搜索轮次 1-3（=collector.search_rounds）"
    ),
    llm_expand: bool = typer.Option(
        False, "--llm-expand", rich_help_panel=_ADVANCED, help="用 LLM 生成多轮查询（=collector.llm_query_expansion）"
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
    deep_search: bool = typer.Option(
        False, "--deep-search", rich_help_panel=_ADVANCED,
        help="深搜：多排序×多页翻页（=collector.deep_search）",
    ),
    deep_pages: Optional[int] = typer.Option(
        None, "--deep-pages", rich_help_panel=_ADVANCED,
        help="深搜页数（=collector.deep_pages，默认 3）",
    ),
    deep_sorts: Optional[str] = typer.Option(
        None, "--deep-sorts", rich_help_panel=_ADVANCED,
        help="深搜排序逗号分隔（=collector.deep_sorts，默认 relevance,date,citations）",
    ),
    relevance_filter: bool = typer.Option(
        False, "--relevance-filter", rich_help_panel=_ADVANCED,
        help="LLM 按主题给清洗后文档评 0-1 分，剔低分（=cleaner.relevance_filter）",
    ),
    profile_iterations: Optional[int] = typer.Option(
        None, "--profile-iterations", rich_help_panel=_ADVANCED,
        help="画像迭代轮数 1-5，≥2 启用时间线回溯+同名消歧（=deepen.profile_iterations）",
    ),
    max_backward_rounds: Optional[int] = typer.Option(
        None, "--max-backward-rounds", rich_help_panel=_ADVANCED,
        help="反向传播轮数 0-3，>0 启用知识树质量评估循环（=pipeline.max_backward_rounds）",
    ),
    pdf_dir: Optional[Path] = typer.Option(
        None, "--pdf-dir", rich_help_panel=_ADVANCED, help="改用本地 PDF 文件夹作为数据源"
    ),
    mineru_cmd: Optional[str] = typer.Option(
        None, "--mineru-cmd", rich_help_panel=_ADVANCED, help="mineru 路径（Web 抓到的 PDF 也用它解析）"
    ),
    translate: bool = typer.Option(
        False, "--translate", rich_help_panel=_ADVANCED, help="PDF 英文 MD 译成中文"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", rich_help_panel=_ADVANCED, help="覆盖 LLM 模型（=llm.model）"
    ),
    video_url: list[str] = typer.Option(
        [], "--video-url", rich_help_panel=_ADVANCED,
        help=(
            "V1.1 VideoIngest：视频 URL（可多次）。一期 P0 仅支持 bilibili.com / b23.tv / "
            "youtube.com / youtu.be。多个 URL 默认 3 并发。"
        ),
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", rich_help_panel=_ADVANCED,
        help="V1.1 VideoIngest：跳过 M-004 转写缓存（强制重转）。",
    ),
) -> None:
    """一键全流程：collect/PDF摄取 → clean → extract → organize → report。

    V1.1 扩展：传 --video-url 时进入 VideoIngest 流程（下载→转写→总结→raw/）。
    """
    # V1.1 VideoIngest 入口：--video-url 优先于其他 stage
    if video_url:
        _run_video_ingest(
            topic=topic,
            video_urls=video_url,
            output=output,
            no_cache=no_cache,
            model=model,
        )
        return

    all_stages = ["collect", "deepen", "clean", "extract", "organize", "report"]
    stages = [s for s in all_stages if s not in skip]

    if dry_run:
        logger.info("将执行的阶段：%s", " → ".join(stages))
        raise typer.Exit()

    overrides = _build_run_overrides(
        topic=topic, output=output, stages=stages, resume=resume,
        source=source, max_results=max_results, rounds=rounds,
        llm_expand=llm_expand, query=query, core=core, facets=facets,
        from_year=from_year, to_year=to_year, deep_search=deep_search,
        deep_pages=deep_pages, deep_sorts=deep_sorts,
        relevance_filter=relevance_filter,
        profile_iterations=profile_iterations,
        max_backward_rounds=max_backward_rounds,
        pdf_dir=pdf_dir, mineru_cmd=mineru_cmd, translate=translate, model=model,
    )

    async def _go():
        cfg = load_config(_state["config_path"], overrides=overrides)
        pipeline = ResearchPipeline(cfg)
        async for ev in pipeline.stream(topic):
            logger.info("%-9s %-9s %s", ev.status, ev.stage, ev.message)
        res = pipeline._result
        if res and res.collect_result:
            _print_warnings(res.collect_result.warnings)
        if res and res.deepen_result:
            _print_warnings(res.deepen_result.warnings)
        if res and res.failed_stage:
            _fail(f"在 {res.failed_stage} 阶段失败")
        if res and res.report_result:
            logger.info("✓ 报告：%s", res.report_result.report_path)
        if res:
            logger.info("耗时 %.1fs", res.elapsed_sec)

    _run(_go())


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
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
        _fail("未安装 Web 界面依赖，请先：pip install \"research-tool[ui]\"（或 pip install gradio）")
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
    data = cfg.model_dump(mode="json")
    if cfg.llm.api_key:
        data["llm"]["api_key"] = "***"
    _out.print_json(json.dumps(data, ensure_ascii=False))


def _run(coro) -> None:
    """统一跑 async + 异常 → stderr。"""
    try:
        asyncio.run(coro)
    except ResearchToolError as e:
        _fail(str(e))
    except KeyboardInterrupt:  # pragma: no cover
        _fail("已中断")


if __name__ == "__main__":
    app()
