"""Pluggable OCR engines for PDF ingestion.

The core package keeps OCR runtimes optional. Heavy model engines are connected
through explicit commands or local model paths instead of importing large
frameworks at module import time.
"""

from __future__ import annotations

import abc
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...domain.errors import StageError
from ...domain.models import PdfIngestConfig


@dataclass(frozen=True)
class OCREngineStatus:
    name: str
    available: bool
    detail: str = ""


class OCREngine(abc.ABC):
    name = "base"

    def __init__(self, config: PdfIngestConfig) -> None:
        self.config = config

    @abc.abstractmethod
    def available(self) -> OCREngineStatus:
        """Return availability without raising."""

    @abc.abstractmethod
    def parse(self, pdf: Path, parse_root: Path) -> str:
        """Parse one PDF into Markdown."""


def _command_exists(cmd: str | None) -> bool:
    if not cmd:
        return False
    return Path(cmd).exists() or shutil.which(cmd) is not None


def _first_markdown(parse_root: Path, stem: str) -> str:
    md_files = list((parse_root / stem).rglob(f"{stem}.md"))
    if not md_files:
        md_files = list((parse_root / stem).rglob("*.md"))
    if not md_files:
        md_files = list(parse_root.rglob("*.md")) + list(parse_root.rglob("*.txt"))
    if not md_files:
        raise StageError("ingest-pdf", f"OCR 未产出 Markdown/Text：{stem}")
    return md_files[0].read_text(encoding="utf-8")


class MineruOCREngine(OCREngine):
    name = "mineru"

    def _cmd(self) -> str:
        return self.config.mineru_cmd or self.config.ocr_cmd or "mineru"

    def available(self) -> OCREngineStatus:
        cmd = self._cmd()
        ok = _command_exists(cmd)
        return OCREngineStatus(self.name, ok, cmd if ok else f"not found: {cmd}")

    def parse(self, pdf: Path, parse_root: Path) -> str:
        status = self.available()
        if not status.available:
            raise StageError(
                "ingest-pdf",
                "未找到 mineru。请安装 MinerU，或用 pdf_ingest.mineru_cmd / "
                "pdf_ingest.ocr_cmd 指定可执行路径。",
            )
        cmd = [
            self._cmd(),
            "-p",
            str(pdf),
            "-o",
            str(parse_root),
            "-b",
            self.config.mineru_backend,
            "-l",
            self.config.ocr_lang,
        ]
        if self.config.start_page is not None:
            cmd += ["-s", str(self.config.start_page)]
        if self.config.end_page is not None:
            cmd += ["-e", str(self.config.end_page)]
        result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603  # trusted external OCR/mineru subprocess
        if result.returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"mineru 解析失败（{pdf.name}, code={result.returncode}）：\n"
                f"{(result.stderr or '')[-800:]}",
            )
        return _first_markdown(parse_root, pdf.stem)


class CommandOCREngine(OCREngine):
    """Generic OCR wrapper.

    If ocr_cmd contains placeholders, they are expanded:
    {pdf}, {out}, {lang}, {model}. Otherwise we append PDF and output dir.
    The command may either print Markdown to stdout or write .md/.txt under out.
    """

    name = "custom"

    def available(self) -> OCREngineStatus:
        if not self.config.ocr_cmd:
            return OCREngineStatus(self.name, False, "pdf_ingest.ocr_cmd is empty")
        return OCREngineStatus(self.name, True, self.config.ocr_cmd)

    def _argv(self, pdf: Path, parse_root: Path) -> list[str]:
        assert self.config.ocr_cmd is not None
        values = {
            "pdf": str(pdf),
            "out": str(parse_root),
            "lang": self.config.ocr_lang,
            "model": self.config.ocr_model_path or "",
        }
        if "{" in self.config.ocr_cmd:
            return shlex.split(self.config.ocr_cmd.format(**values), posix=os.name != "nt")
        return shlex.split(self.config.ocr_cmd, posix=os.name != "nt") + [str(pdf), str(parse_root)]

    def parse(self, pdf: Path, parse_root: Path) -> str:
        if not self.config.ocr_cmd:
            raise StageError("ingest-pdf", "custom OCR 需要配置 pdf_ingest.ocr_cmd")
        result = subprocess.run(self._argv(pdf, parse_root), capture_output=True, text=True)  # noqa: S603  # trusted external OCR/mineru subprocess
        if result.returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"{self.name} OCR 失败（{pdf.name}, code={result.returncode}）：\n"
                f"{(result.stderr or '')[-800:]}",
            )
        if result.stdout.strip():
            return result.stdout
        return _first_markdown(parse_root, pdf.stem)


class ModelCommandOCREngine(CommandOCREngine):
    """Named model OCR wrapper, still command-driven to keep heavy deps optional."""

    def available(self) -> OCREngineStatus:
        base = super().available()
        if not base.available:
            return base
        model = self.config.ocr_model_path
        if model and not Path(model).exists():
            return OCREngineStatus(self.name, False, f"model path not found: {model}")
        return OCREngineStatus(self.name, True, base.detail)


class PaddleOCRVLEngine(ModelCommandOCREngine):
    name = "paddleocr-vl"


class UnlimitedOCREngine(ModelCommandOCREngine):
    name = "unlimited-ocr"


class VisionLLMOCREngine(CommandOCREngine):
    name = "vision-llm"


_ENGINES: dict[str, type[OCREngine]] = {
    "mineru": MineruOCREngine,
    "custom": CommandOCREngine,
    "paddleocr-vl": PaddleOCRVLEngine,
    "unlimited-ocr": UnlimitedOCREngine,
    "vision-llm": VisionLLMOCREngine,
}


def scan_ocr_engines(config: PdfIngestConfig | None = None) -> list[OCREngineStatus]:
    cfg = config or PdfIngestConfig()
    return [engine(cfg).available() for engine in _ENGINES.values()]


def create_ocr_engine(config: PdfIngestConfig) -> OCREngine:
    if config.ocr_engine == "auto":
        for status in scan_ocr_engines(config):
            if status.available:
                return _ENGINES[status.name](config)
        raise StageError("ingest-pdf", "未发现可用 OCR 引擎")
    try:
        engine_cls = _ENGINES[config.ocr_engine]
    except KeyError as e:
        raise StageError("ingest-pdf", f"未知 OCR 引擎: {config.ocr_engine}") from e
    return engine_cls(config)


__all__ = [
    "OCREngineStatus",
    "OCREngine",
    "scan_ocr_engines",
    "create_ocr_engine",
]
