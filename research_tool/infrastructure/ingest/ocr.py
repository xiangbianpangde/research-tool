"""Pluggable OCR engines for PDF ingestion.

The core package keeps OCR runtimes optional. Heavy model engines are connected
through explicit commands or local model paths instead of importing large
frameworks at module import time.
"""

from __future__ import annotations

import abc
import asyncio
import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ...domain.errors import StageError
from ...domain.models import PdfIngestConfig


@dataclass(frozen=True)
class OCREngineStatus:
    name: str
    available: bool
    detail: str = ""


OCR_TIMEOUT_SEC = 300.0
OCR_CLEANUP_TIMEOUT_SEC = 1.0


class OCREngineCleanupTimeoutError(TimeoutError):
    """OCR child cleanup did not finish within its bounded deadline."""


def _process_group_kwargs() -> dict[str, object]:
    # The supported acceptance target is macOS/POSIX. Windows gets direct-child
    # process-group best effort only; recursive taskkill is intentionally out of scope.
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_and_reap(
    process: subprocess.Popen[bytes], *, deadline_sec: float = OCR_CLEANUP_TIMEOUT_SEC
) -> None:
    """Terminate a sync OCR process group and reap it within one deadline."""
    deadline = time.monotonic() + max(float(deadline_sec), 0.001)

    def remaining() -> float:
        return max(deadline - time.monotonic(), 0.001)

    def wait_or_timeout() -> None:
        try:
            process.wait(timeout=remaining())
        except subprocess.TimeoutExpired as e:
            raise OCREngineCleanupTimeoutError(
                f"OCR child cleanup/reap exceeded {max(float(deadline_sec), 0.001):g}s"
            ) from e

    pid = process.pid
    if process.poll() is None:
        if os.name != "nt":
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            wait_or_timeout()
        except OCREngineCleanupTimeoutError:
            if os.name != "nt":
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            wait_or_timeout()

    try:
        process.communicate(timeout=remaining())
    except subprocess.TimeoutExpired as e:
        if process.poll() is None:
            if os.name != "nt":
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
        try:
            process.communicate(timeout=remaining())
        except subprocess.TimeoutExpired as final_error:
            raise OCREngineCleanupTimeoutError(
                f"OCR child cleanup/reap exceeded {max(float(deadline_sec), 0.001):g}s"
            ) from final_error
        raise OCREngineCleanupTimeoutError(
            f"OCR child cleanup/reap exceeded {max(float(deadline_sec), 0.001):g}s"
        ) from e


async def _kill_and_reap_async(
    process: asyncio.subprocess.Process, *, deadline_sec: float = OCR_CLEANUP_TIMEOUT_SEC
) -> None:
    """Terminate an async OCR process group and wait within a total deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(deadline_sec), 0.001)

    async def wait_until_deadline() -> bool:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        try:
            await asyncio.wait_for(process.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return False
        return True

    if process.returncode is None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        if await wait_until_deadline():
            return

    if process.returncode is None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        if await wait_until_deadline():
            return

    if process.returncode is not None:
        return
    raise OCREngineCleanupTimeoutError(
        f"OCR child cleanup/reap exceeded {max(float(deadline_sec), 0.001):g}s"
    )


class OCREngine(abc.ABC):
    name = "base"

    def __init__(self, config: PdfIngestConfig, *, timeout_sec: float = OCR_TIMEOUT_SEC) -> None:
        self.config = config
        self.timeout_sec = max(float(timeout_sec), 0.001)

    @abc.abstractmethod
    def available(self) -> OCREngineStatus:
        """Return availability without raising."""

    @abc.abstractmethod
    def parse(self, pdf: Path, parse_root: Path) -> str:
        """Parse one PDF into Markdown."""

    async def parse_async(self, pdf: Path, parse_root: Path) -> str:
        """Async boundary; subprocess-backed engines override this.

        The fallback intentionally calls the synchronous API directly rather than
        hiding an uncancellable third-party thread behind ``to_thread``.
        """
        return self.parse(pdf, parse_root)


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


def _run_command_sync(argv: list[str], timeout_sec: float) -> tuple[int, str, str]:
    process = subprocess.Popen(  # noqa: S603  # trusted external OCR/mineru subprocess
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_process_group_kwargs(),
    )  # noqa: S603  # trusted external OCR/mineru subprocess
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        try:
            _kill_and_reap(process)
        except subprocess.TimeoutExpired as cleanup_error:
            raise StageError("ingest-pdf", "OCR 子进程清理超时") from cleanup_error
        raise StageError("ingest-pdf", f"OCR 子进程超时（>{timeout_sec:g}s）") from e
    return process.returncode, stdout or "", stderr or ""


async def _run_command_async(argv: list[str], timeout_sec: float) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_process_group_kwargs(),
    )  # noqa: S603  # trusted external OCR/mineru subprocess
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError as e:
        await _kill_and_reap_async(process)
        raise StageError("ingest-pdf", f"OCR 子进程超时（>{timeout_sec:g}s）") from e
    except asyncio.CancelledError:
        await _kill_and_reap_async(process)
        raise
    stdout_text = (stdout or b"").decode(errors="replace")
    stderr_text = (stderr or b"").decode(errors="replace")
    return process.returncode or 0, stdout_text, stderr_text


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
        returncode, _stdout, stderr = _run_command_sync(cmd, self.timeout_sec)
        if returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"mineru 解析失败（{pdf.name}, code={returncode}）：\n"
                f"{stderr[-800:]}",
            )
        return _first_markdown(parse_root, pdf.stem)

    async def parse_async(self, pdf: Path, parse_root: Path) -> str:
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
        returncode, _stdout, stderr = await _run_command_async(cmd, self.timeout_sec)
        if returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"mineru 解析失败（{pdf.name}, code={returncode}）：\n"
                f"{stderr[-800:]}",
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
        returncode, stdout, stderr = _run_command_sync(
            self._argv(pdf, parse_root), self.timeout_sec
        )
        if returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"{self.name} OCR 失败（{pdf.name}, code={returncode}）：\n"
                f"{stderr[-800:]}",
            )
        if stdout.strip():
            return stdout
        return _first_markdown(parse_root, pdf.stem)

    async def parse_async(self, pdf: Path, parse_root: Path) -> str:
        if not self.config.ocr_cmd:
            raise StageError("ingest-pdf", "custom OCR 需要配置 pdf_ingest.ocr_cmd")
        returncode, stdout, stderr = await _run_command_async(
            self._argv(pdf, parse_root), self.timeout_sec
        )
        if returncode != 0:
            raise StageError(
                "ingest-pdf",
                f"{self.name} OCR 失败（{pdf.name}, code={returncode}）：\n"
                f"{stderr[-800:]}",
            )
        if stdout.strip():
            return stdout
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
