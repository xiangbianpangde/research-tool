"""命令行接口。

依据 02-CLI接口设计.md。CLI 是薄封装，直接调用核心引擎；正常输出到 stdout，
错误到 stderr（02 §10）；进度用 rich。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

# 把输出流设为 UTF-8 且编码失败时替换，避免在 GBK 控制台上打印 ✓/→ 等字符崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from . import __version__
from .config import load_config
from .errors import ResearchToolError
from .llm.base import LLMClient
from .models import (
    CleanerConfig,
    CollectorConfig,
    ExtractorConfig,
    OrganizerConfig,
    PdfIngestConfig,
    ReporterConfig,
)
from .pipeline import ResearchPipeline
from .slug import slugify
from .stages import Cleaner, Collector, Extractor, Organizer, Reporter

app = typer.Typer(
    add_completion=False,
    help="research —— 给定主题，产出知识树/调研报告",
    no_args_is_help=True,
)

out = Console()
err = Console(stderr=True)

# 全局状态（由回调填充）
_state: dict = {"config_path": None, "verbose": False, "quiet": False}

# run 命令的高级选项分组标题（问题 5：--help 主面板只显常用项，调优项折叠）
_ADVANCED = "高级选项（低频；等价项可写入 config.yaml）"


def _version_cb(value: bool) -> None:
    if value:
        out.print(f"research-tool {__version__}")
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


def _log(msg: str) -> None:
    if not _state["quiet"]:
        out.print(msg)


def _fail(msg: str) -> None:
    err.print(f"[red]错误[/red] {msg}")
    raise typer.Exit(code=1)


def _print_warnings(warnings: list[str]) -> None:
    """搜索/深挖告警输出到 stderr（修复 1：可观测）。"""
    for w in warnings or []:
        err.print(f"[yellow]警告[/yellow] {w}")


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
    )
    topic_dir = output / slugify(topic)

    async def _go():
        c = Collector(cfg, _make_llm() if llm_expand else None)
        if dry_run:
            sr = await c.search_only(topic)
            for h in sr.hits:
                out.print(f"[cyan]{h.source_engine}[/cyan] {h.title}\n  {h.url}")
            _log(f"\n共 {len(sr.hits)} 条结果（dry-run，未抓取）")
            _print_warnings(sr.warnings)
            return
        res = await c.run(topic, topic_dir)
        _log(f"采集完成：{len(res.files)} 个文件 → {res.raw_dir}")
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
    from .ingest import PdfIngestor

    cfg = PdfIngestConfig(
        mineru_backend=backend, ocr_lang=lang, translate=translate, mineru_cmd=mineru_cmd
    )
    topic_dir = output / slugify(topic)

    async def _go():
        llm = _make_llm(model) if translate else None
        res = await PdfIngestor(cfg, llm).run(pdf_path, topic_dir)
        _log(f"PDF 摄取完成：{len(res.files)} 个文件 → {res.raw_dir}")
        if translate:
            _log("（已翻译为中文）")

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
    _log(f"清洗完成：{len(res.files)} 个文件 → {res.clean_dir}")


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
        _log(
            f"抽取完成：{len(res.entities)} 实体 / {len(res.relations)} 关系 / "
            f"{len(res.triples)} 三元组 → {res.output_dir}"
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
        _log(f"组织完成：{len(res.nodes)} 个节点 → {res.tree_dir}")

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
        _log(f"报告完成：{res.word_count} 字 → {res.report_path}")

    _run(_go())


# --------------------------------------------------------------------------- #
# run（一键全流程）
# --------------------------------------------------------------------------- #
@app.command()
def run(
    topic: str = typer.Argument(..., help="调研主题"),
    # --- 常用 5 项（问题 5：run 主面板只留最常用，调优归 config.yaml）--- #
    source: list[str] = typer.Option(["web"], "-s", "--source", help="搜索来源（可多次）"),
    output: Path = typer.Option(Path("./research-output"), "-o", "--output", help="输出目录"),
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
) -> None:
    """一键全流程：collect/PDF摄取 → clean → extract → organize → report。"""
    all_stages = ["collect", "deepen", "clean", "extract", "organize", "report"]
    stages = [s for s in all_stages if s not in skip]

    if dry_run:
        _log("将执行的阶段：" + " → ".join(stages))
        raise typer.Exit()

    overrides = {
        "topic": topic,
        "work_dir": str(output),
        "stages": stages,
        "resume": resume,
        "collector": {
            "search_engines": source,
            "max_results_per_engine": max_results,
            "search_rounds": rounds,
            "llm_query_expansion": llm_expand,
            "extra_queries": list(query),
        },
    }
    if mineru_cmd:
        overrides["collector"]["mineru_cmd"] = mineru_cmd
    if pdf_dir:
        overrides["pdf_dir"] = str(pdf_dir)
        overrides["pdf_ingest"] = {"translate": translate}
        if mineru_cmd:
            overrides["pdf_ingest"]["mineru_cmd"] = mineru_cmd
    if model:
        overrides["llm"] = {"model": model}

    async def _go():
        cfg = load_config(_state["config_path"], overrides=overrides)
        pipeline = ResearchPipeline(cfg)
        async for ev in pipeline.stream(topic):
            color = {"failed": "red", "skipped": "yellow", "completed": "green"}.get(
                ev.status, "cyan"
            )
            _log(f"[{color}]{ev.status:9}[/{color}] {ev.stage:9} {ev.message}")
        res = pipeline._result
        if res and res.collect_result:
            _print_warnings(res.collect_result.warnings)
        if res and res.deepen_result:
            _print_warnings(res.deepen_result.warnings)
        if res and res.failed_stage:
            _fail(f"在 {res.failed_stage} 阶段失败")
        if res and res.report_result:
            _log(f"\n✓ 报告：{res.report_result.report_path}")
        if res:
            _log(f"耗时 {res.elapsed_sec:.1f}s")

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
    out.print(table)


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
        from .webui import main as ui_main
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
    out.print_json(json.dumps(data, ensure_ascii=False))


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
