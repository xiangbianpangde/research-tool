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
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..errors import StageError
from ..llm.base import LLMClient
from ..models import CollectResult, PdfIngestConfig, Source
from ..stages.base import ensure_dir, safe_filename, write_json, write_text
from ..translate import translate_markdown


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_mineru(cmd: str | None) -> str:
    """定位 mineru 可执行；找不到抛清晰错误（含安装提示）。"""
    candidate = cmd or "mineru"
    if Path(candidate).exists() or shutil.which(candidate):
        return candidate
    raise StageError(
        "ingest-pdf",
        "未找到 mineru。请安装 MinerU（pip install mineru，约 7GB 含模型），"
        "或用 pdf_ingest.mineru_cmd 指定 pdf2zh 虚拟环境里的 mineru 路径，例如 "
        r"C:\Users\you\Desktop\pdf2zh-v0.1.0\pdf2zh\.venv\Scripts\mineru.exe",
    )


def find_mineru(cmd: str | None = None) -> str | None:
    """返回可用的 mineru 路径，找不到返回 None（不抛错，供采集路径探测）。"""
    candidate = cmd or "mineru"
    if Path(candidate).exists() or shutil.which(candidate):
        return candidate
    return None


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
    cmd = [mineru, "-p", str(pdf), "-o", str(parse_root), "-b", backend, "-l", lang]
    if start is not None:
        cmd += ["-s", str(start)]
    if end is not None:
        cmd += ["-e", str(end)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise StageError(
            "ingest-pdf",
            f"mineru 解析失败（{pdf.name}, code={result.returncode}）：\n"
            f"{(result.stderr or '')[-800:]}",
        )
    md_files = list((parse_root / pdf.stem).rglob(f"{pdf.stem}.md"))
    if not md_files:
        md_files = list((parse_root / pdf.stem).rglob("*.md"))
    if not md_files:
        raise StageError("ingest-pdf", f"mineru 未产出 Markdown：{pdf.name}")
    return md_files[0].read_text(encoding="utf-8")


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
        mineru = _resolve_mineru(self.config.mineru_cmd)

        work_dir = Path(work_dir)
        raw_dir = ensure_dir(work_dir / "raw")
        parse_root = ensure_dir(work_dir / "_mineru")  # MinerU 中间产物

        files: list[Path] = []
        sources: list[Source] = []
        for idx, pdf in enumerate(pdfs, 1):
            # MinerU 是 CPU/GPU 密集子进程，逐个跑（避免显存竞争）
            content = await asyncio.to_thread(
                self._run_mineru, pdf, mineru, parse_root
            )
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
                f"<!-- via: mineru({self.config.mineru_backend})"
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
