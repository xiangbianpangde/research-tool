"""PDF 摄取适配器 —— 集成 pdf2zh / MinerU。

把一个文件夹（或若干）学术 PDF 转成 raw/NN-{stem}.md，供 research-tool 的
clean → extract → organize → report 接力。可选用 LLMClient 把英文 MD 翻成中文。

MinerU 是可选重依赖（约 7GB，含模型）。本模块只通过 `mineru` CLI 子进程调用，
未安装时给出清晰报错；与 research-tool 其余部分零强依赖。

数据流：
    pdf_dir/*.pdf ──mineru──> 英文 MD ──(可选)LLM 翻译──> raw/NN-{stem}.md
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from ...domain.errors import StageError
from ..llm.base import LLMClient
from ...domain.models import CollectResult, PdfIngestConfig, Source
from ..stages.base import ensure_dir, safe_filename, write_json, write_text
from ...common.translate import translate_markdown
from .ocr import create_ocr_engine, scan_ocr_engines


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_mineru(cmd: str | None) -> str:
    """定位 mineru 可执行；找不到抛清晰错误（含安装提示）。"""
    cfg = PdfIngestConfig(ocr_engine="mineru", mineru_cmd=cmd)
    status = scan_ocr_engines(cfg)[0]
    if status.available:
        return cmd or "mineru"
    raise StageError("ingest-pdf", "未找到 mineru。请安装 MinerU 或指定 mineru_cmd。")


def find_mineru(cmd: str | None = None) -> str | None:
    """返回可用的 mineru 路径，找不到返回 None（不抛错，供采集路径探测）。"""
    cfg = PdfIngestConfig(ocr_engine="mineru", mineru_cmd=cmd)
    return (cmd or "mineru") if scan_ocr_engines(cfg)[0].available else None


def mineru_to_markdown(
    pdf: Path,
    mineru: str,
    parse_root: Path,
    *,
    backend: str = "pipeline",
    lang: str = "ch",
    start: int | None = None,
    end: int | None = None,
) -> str:
    """调用 mineru 解析单个 PDF，返回其 Markdown 文本（找不到产物则抛 StageError）。"""
    cfg = PdfIngestConfig(
        ocr_engine="mineru",
        mineru_cmd=mineru,
        mineru_backend=backend,
        ocr_lang=lang,
        start_page=start,
        end_page=end,
    )
    return create_ocr_engine(cfg).parse(pdf, parse_root)


def _collect_pdfs(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.pdf"))
    raise StageError("ingest-pdf", f"路径不是 PDF 文件或目录: {path}")


class PdfIngestor:
    def __init__(
        self, config: PdfIngestConfig | None = None, llm: LLMClient | None = None
    ) -> None:
        self.config = config or PdfIngestConfig()
        self.llm = llm
        if self.config.translate and self.llm is None:
            raise StageError("ingest-pdf", "translate=True 需要提供 llm")

    def _run_mineru(self, pdf: Path, mineru: str, parse_root: Path) -> str:
        """调用 mineru 解析单个 PDF，返回其 Markdown 文本。"""
        return mineru_to_markdown(
            pdf, mineru, parse_root,
            backend=self.config.mineru_backend, lang=self.config.ocr_lang,
            start=self.config.start_page, end=self.config.end_page,
        )

    async def run(self, pdf_path: Path, work_dir: Path) -> CollectResult:
        pdfs = _collect_pdfs(Path(pdf_path))
        if not pdfs:
            raise StageError("ingest-pdf", f"未找到 PDF：{pdf_path}")
        work_dir = Path(work_dir)
        raw_dir = ensure_dir(work_dir / "raw")
        parse_root = ensure_dir(work_dir / "_ocr")  # OCR 中间产物
        ocr_engine = create_ocr_engine(self.config)
        status = ocr_engine.available()
        if not status.available:
            raise StageError("ingest-pdf", f"OCR 引擎不可用: {status.name} ({status.detail})")

        files: list[Path] = []
        sources: list[Source] = []
        for idx, pdf in enumerate(pdfs, 1):
            # MinerU 是 CPU/GPU 密集子进程，逐个跑（避免显存竞争）
            content = await asyncio.to_thread(ocr_engine.parse, pdf, parse_root)
            translated = False
            if self.config.translate and self.llm is not None:
                content = await translate_markdown(
                    content,
                    self.llm,
                    chunk_size=self.config.translate_chunk_size,
                    concurrency=self.config.translate_concurrency,
                )
                translated = True

            fname = f"{idx:02d}-{safe_filename(pdf.stem)}.md"
            fpath = raw_dir / fname
            header = (
                f"<!-- source: file://{pdf.resolve()} -->\n"
                f"<!-- title: {pdf.stem} -->\n"
                f"<!-- fetched: {_now_iso()} -->\n"
                f"<!-- via: ocr:{ocr_engine.name}"
                f"{' + translate' if translated else ''} -->\n\n"
            )
            write_text(fpath, header + content)
            files.append(fpath)
            sources.append(
                Source(
                    url=f"file://{pdf.resolve()}",
                    title=pdf.stem,
                    fetched_at=_now_iso(),
                    source_engine="pdf",
                    content_hash=_sha256(content),
                )
            )

        write_json(raw_dir / "sources.json", [s.model_dump() for s in sources])
        return CollectResult(files=files, sources=sources, raw_dir=raw_dir)


async def ingest_pdfs(
    pdf_path: Path,
    work_dir: Path,
    config: PdfIngestConfig | None = None,
    llm: LLMClient | None = None,
) -> CollectResult:
    """便捷函数：把 PDF（文件或目录）摄取为 raw/。"""
    return await PdfIngestor(config, llm).run(pdf_path, work_dir)
