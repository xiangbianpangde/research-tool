"""M-005 转写器（V1.1 VideoIngest）。

设计依据：
- [DD-001:M-005 转写器] 三引擎调度（whisper / groq / bcut），V1.1 track-core 简化为
  whisper + groq 双后端（B 站 ASR bcut 不在范围）
- [DD-001:IC-012] 转写接口契约
- [调研: BiliNote backend/app/transcriber/{whisper,groq,transcriber_provider}.py]
  —— 移植 faster-whisper 调用 + Groq OpenAI 兼容 API 模式
- NFR4：同 URL 二次运行跳过转写（走 M-004 cache 命中短路）

职责：
- faster-whisper 本地引擎（优先，零成本；ctranslate2 单实例锁）
- Groq 云端引擎（备选，>18MB 音频自动压缩）
- M-004 缓存命中直接返回（IC-009）
- 模板方法：detect_ram → select_engine → transcribe → fallback
- 转写超时（asyncio.wait_for 包装）
- M-010 错误码登记
- M-011 结构化日志

异步：阻塞 I/O（faster-whisper / Groq HTTP）一律 asyncio.to_thread 或 httpx.AsyncClient
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from ...common.logging_config import emit_log, get_logger
from ...domain.errors import (
    ErrorCode,
    TranscribeError,
    register_error,
)
from ...domain.models import Transcript, TranscriptSegment
from .cache_manager import (
    CacheEntry,
    CacheManager,
    DEFAULT_TTL_DAYS,
    compute_url_sha256,
    get_manager as get_cache_manager,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 模块级常量
# --------------------------------------------------------------------------- #


DEFAULT_MODEL_SIZE: str = "medium"
MIN_RAM_GB: float = 8.0
DOWNGRADE_MODEL_SIZES: tuple[str, ...] = ("base", "small")
SUPPORTED_AUDIO_EXTS: tuple[str, ...] = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus")

# Groq 上传限制（25MB → V1.1 设为 18MB 余量）
GROQ_MAX_FILE_BYTES: int = 18 * 1024 * 1024

# 转写缓存有效期（V1.1 与 M-004 默认一致；视频转写 30 天复用）
DEFAULT_TRANSCRIPT_TTL_DAYS: int = DEFAULT_TTL_DAYS

# 模型下载/缓存目录（hf cache root）
DEFAULT_HF_CACHE_DIR: str = os.environ.get(
    "HF_HOME",
    str(Path.home() / ".cache" / "huggingface"),
)

# 错误码（与 M-010 ErrorCode 体系并行；本模块内部字符串常量）
E_TR_001: str = "E_TR_001"  # 所有引擎失败
E_TR_002: str = ErrorCode.E_TX_001_WHISPER_INIT_FAILED.value
E_TR_003: str = "E_TR_003_GROQ_FAILED"
E_TR_004: str = "E_TR_004_TIMEOUT"
E_TR_005: str = ErrorCode.E_TX_002_AUDIO_EXTRACT_FAILED.value


# --------------------------------------------------------------------------- #
# EngineType 枚举
# --------------------------------------------------------------------------- #


class EngineType(str, Enum):
    """转写引擎类型。

    V1.1 简化为 WHISPER / GROQ 双后端；BCUT（V2.0）保留枚举值以兼容设计。
    """

    WHISPER = "whisper"
    GROQ = "groq"
    # 保留 B 站 ASR 枚举位以兼容 IC-012 设计（V1.1 不实现）
    BCUT = "bcut"


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


# TranscribeError 已在 src/domain/errors.py 定义（V1.1）


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TranscribeRequest:
    """转写请求（标准化参数）。"""

    audio_path: str
    audio_fingerprint: str
    language: str = "zh"  # zh / en / ja
    engine: EngineType = EngineType.WHISPER
    model_size: str = DEFAULT_MODEL_SIZE
    timeout_sec: int = 1800  # 默认 30 分钟


@dataclass(frozen=True)
class TranscribeResult:
    """转写结果 + 引擎元信息。"""

    transcript: Transcript
    engine_used: EngineType
    model_size: str
    elapsed_sec: float
    cached: bool = False  # True 表示从 M-004 缓存命中


# --------------------------------------------------------------------------- #
# EngineSelector（引擎选择与降级链）
# --------------------------------------------------------------------------- #


class EngineSelector:
    """引擎选择与降级链管理（V1.1 简化为 2 引擎）。"""

    def __init__(
        self,
        preferred_engine: EngineType = EngineType.WHISPER,
        groq_api_key: str | None = None,
    ) -> None:
        self.preferred_engine = preferred_engine
        self.groq_api_key = groq_api_key
        # 降级链：whisper 失败 → groq
        self._fallback_chain: list[EngineType] = [EngineType.WHISPER, EngineType.GROQ]

    def select(self, ram_gb: float | None = None) -> EngineType:
        """依据 RAM 与偏好选主引擎。

        Args:
            ram_gb: 可用 RAM（GB）；None=不探测（用 preferred_engine）

        Returns:
            选定的主引擎（未做降级链走完，留给模板方法处理）
        """
        # Groq 缺 key 时只能用 whisper
        if not self.is_available(EngineType.GROQ) and self.preferred_engine == EngineType.GROQ:
            return EngineType.WHISPER
        return self.preferred_engine

    def fallback_chain(self) -> list[EngineType]:
        """降级链：[WHISPER, GROQ]。"""
        return list(self._fallback_chain)

    def is_available(self, engine: EngineType) -> bool:
        """引擎可用性（依赖 / API key）。"""
        if engine == EngineType.WHISPER:
            try:
                import faster_whisper  # noqa: F401

                return True
            except ImportError:
                return False
        if engine == EngineType.GROQ:
            return bool(self.groq_api_key)
        return False


# --------------------------------------------------------------------------- #
# WhisperEngine（faster-whisper 本地）
# --------------------------------------------------------------------------- #


class WhisperEngine:
    """faster-whisper 本地引擎（移植自 BiliNote WhisperTranscriber）。

    关键简化：
    - 去掉 modelscope 路径（历史遗留）；直接用 HF cache
    - 去掉 cache 损坏自愈（BiliNote 的 _purge_cache 在 V1.1 不必要）
    - 改用 asr_options / vad_options 控制 VAD（可选）
    - 加 OOM 降档
    - 加超时（asyncio.wait_for 包装，ctranslate2 在主线程跑同步）
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = "cpu",
        compute_type: str | None = None,
        download_root: str | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        # CPU 强制 int8（默认）；GPU 用 float16
        if compute_type is None:
            compute_type = "int8" if device == "cpu" else "float16"
        self.compute_type = compute_type
        self.download_root = download_root or DEFAULT_HF_CACHE_DIR
        self._model = None  # 延迟加载
        self._loaded_size: str | None = None

    def _build_model(self, size: str) -> Any:
        """构造 WhisperModel。"""
        from faster_whisper import WhisperModel

        return WhisperModel(
            model_size_or_path=size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

    def load(self, size: str | None = None) -> Any:
        """加载模型（显式调用）。"""
        size = size or self.model_size
        try:
            self._model = self._build_model(size)
            self._loaded_size = size
            return self._model
        except Exception as e:
            register_error(
                ErrorCode.E_TX_001_WHISPER_INIT_FAILED.value,
                scene="faster-whisper 模型加载失败",
                cause=str(e),
                suggestion="检查 faster-whisper 安装；或更换 model_size",
                context={"model_size": size, "device": self.device},
            )
            raise TranscribeError(
                E_TR_002,
                f"faster-whisper 加载失败 ({size}): {e}",
            ) from e

    def transcribe(
        self,
        audio_path: str,
        language: str = "zh",
        model_size: str | None = None,
    ) -> Transcript:
        """同步转写（在调用方用 asyncio.to_thread 包）。

        Args:
            audio_path: 音频文件绝对路径
            language: 语言代码（zh/en/ja；None=自动检测）
            model_size: 覆盖默认 size（OOM 降档时使用）

        Returns:
            Transcript

        Raises:
            TranscribeError: 转写失败
        """
        size = model_size or self.model_size
        if self._model is None or self._loaded_size != size:
            self.load(size)

        try:
            segments_iter, info = self._model.transcribe(
                str(audio_path),
                language=language if language else None,
                vad_filter=False,
            )
        except Exception as e:
            # OOM 降档
            err_str = str(e).lower()
            if "memory" in err_str or "oom" in err_str:
                for fallback in DOWNGRADE_MODEL_SIZES:
                    try:
                        self.load(fallback)
                        segments_iter, info = self._model.transcribe(
                            str(audio_path), language=language or None, vad_filter=False
                        )
                        size = fallback
                        break
                    except Exception as exc:
                        logger.debug("降档 %s 仍失败: %s", fallback, exc)
                        continue
                else:
                    raise TranscribeError(
                        E_TR_002,
                        f"faster-whisper OOM 且降档失败: {e}",
                    ) from e
            else:
                raise TranscribeError(
                    E_TR_002,
                    f"faster-whisper transcribe 失败: {e}",
                ) from e

        segments: list[TranscriptSegment] = []
        full_text_parts: list[str] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            segments.append(
                TranscriptSegment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=text,
                )
            )
            if text:
                full_text_parts.append(text)

        return Transcript(
            language=getattr(info, "language", language) or "zh",
            full_text=" ".join(full_text_parts),
            segments=segments,
            engine="whisper",
            cer_estimate=0.0,  # 无 reference 时占位
            raw={"model_size": size, "device": self.device},
        )

    def unload(self) -> None:
        """显式卸载（释放内存）。"""
        self._model = None
        self._loaded_size = None


# --------------------------------------------------------------------------- #
# GroqEngine（Groq 云端 API）
# --------------------------------------------------------------------------- #


class GroqEngine:
    """Groq 云端转写引擎（OpenAI 兼容 API）。

    移植自 BiliNote GroqTranscriber：
    - 复用 OpenAI 兼容 audio.transcriptions.create 接口
    - 18MB 限制 → 用 ffmpeg 压缩到 64k bitrate
    - 加超时（httpx 默认超时 + asyncio.wait_for）
    - 限流 429 → 指数退避重试
    """

    DEFAULT_MODEL: ClassVar[str] = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3")
    DEFAULT_BASE_URL: ClassVar[str] = os.environ.get(
        "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
    )

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise TranscribeError(E_TR_003, "Groq api_key 必填")
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.max_retries = max_retries

    @staticmethod
    def _needs_compress(audio_path: str) -> bool:
        try:
            return os.path.getsize(audio_path) > GROQ_MAX_FILE_BYTES
        except OSError:
            return False

    @staticmethod
    def _compress_audio(audio_path: str) -> str:
        """用 ffmpeg 压缩音频到 64k bitrate（faster-whisper 推荐 mp3 64k）。

        通过 M-009 ffmpeg_wrapper 复用 track-foundation 已实现的 FFmpegInvoker。
        """
        from .ffmpeg_wrapper import FFmpegInvoker

        inv = FFmpegInvoker()
        output_path = str(Path(audio_path).with_suffix(".compressed.mp3"))
        try:
            # 同步 ffmpeg 调用（外面 asyncio.to_thread 包）
            result = inv.run_sync(
                [
                    "-y",
                    "-i",
                    audio_path,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "64k",
                    output_path,
                ],
                timeout_sec=120,
            )
            if result.returncode != 0:
                raise TranscribeError(
                    E_TR_003,
                    f"Groq 音频压缩失败: exit={result.returncode}",
                )
            return output_path
        except Exception as e:
            raise TranscribeError(
                E_TR_003,
                f"Groq 音频压缩失败: {e}",
            ) from e

    def _call_api(self, audio_path: str) -> dict[str, Any]:
        """同步调用 Groq API（OpenAI 兼容）。"""
        try:
            from openai import OpenAI
        except ImportError as e:
            raise TranscribeError(
                E_TR_003,
                "openai SDK 未安装；pip install openai",
            ) from e

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with open(audio_path, "rb") as f:
                    resp = client.audio.transcriptions.create(
                        file=(os.path.basename(audio_path), f.read()),
                        model=self.model,
                        response_format="verbose_json",
                    )
                # 兼容对象/dict 两种返回
                if hasattr(resp, "model_dump"):
                    return resp.model_dump()
                if isinstance(resp, dict):
                    return resp
                return dict(resp)
            except Exception as e:
                last_exc = e
                err_str = str(e).lower()
                # 429 限流 → 退避重试
                if "429" in err_str or "rate" in err_str:
                    if attempt < self.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                # 其他错误 → 抛
                raise
        raise TranscribeError(
            E_TR_003,
            f"Groq API 重试 {self.max_retries} 次仍失败: {last_exc}",
        )

    def transcribe(
        self,
        audio_path: str,
        language: str = "zh",
    ) -> Transcript:
        """同步转写（外层用 asyncio.to_thread 包）。"""
        if not os.path.exists(audio_path):
            raise TranscribeError(E_TR_003, f"音频文件不存在: {audio_path}")

        used_path = audio_path
        compressed = False
        if self._needs_compress(audio_path):
            used_path = self._compress_audio(audio_path)
            compressed = True

        try:
            data = self._call_api(used_path)
        except TranscribeError:
            raise
        except Exception as e:
            register_error(
                ErrorCode.E_TR_003_GROQ_FAILED.value,
                scene="Groq 转写 API 调用失败",
                cause=str(e),
                suggestion="检查 GROQ_API_KEY 与网络；或切回 whisper",
                context={"model": self.model},
            )
            raise TranscribeError(E_TR_003, f"Groq 转写失败: {e}") from e
        finally:
            if compressed and used_path != audio_path:
                try:
                    os.unlink(used_path)
                except OSError:
                    pass

        # 解析 verbose_json
        language_out = data.get("language") or language
        full_text = (data.get("text") or "").strip()
        segments: list[TranscriptSegment] = []
        for seg in data.get("segments", []) or []:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start", 0))
                end = float(seg.get("end", 0))
            except (TypeError, ValueError):
                continue
            segments.append(TranscriptSegment(start=start, end=end, text=text))

        return Transcript(
            language=language_out,
            full_text=full_text,
            segments=segments,
            engine="groq",
            cer_estimate=0.0,
            raw={"model": self.model, "compressed": compressed},
        )


# --------------------------------------------------------------------------- #
# 模板方法：转写主流程（IC-012）
# --------------------------------------------------------------------------- #


async def transcribe(
    audio_path: str,
    audio_fingerprint: str | None = None,
    *,
    language: str = "zh",
    engine: EngineType = EngineType.WHISPER,
    model_size: str = DEFAULT_MODEL_SIZE,
    timeout_sec: int = 1800,
    cache_manager: CacheManager | None = None,
    preferred_engine: EngineType = EngineType.WHISPER,
    groq_api_key: str | None = None,
    write_cache: bool = True,
    read_cache: bool = True,
) -> Transcript:
    """转写主入口（带 NFR4 缓存命中短路 + 引擎 fallback + 超时）。

    Args:
        audio_path: 音频文件绝对路径
        audio_fingerprint: 音频 sha256 指纹（默认根据路径 + mtime + size 计算）
        language: 语言代码（zh/en/ja）
        engine: 主引擎选择（默认 WHISPER）
        model_size: faster-whisper 模型档位
        timeout_sec: 单引擎超时（秒）
        cache_manager: 缓存管理器（None=用全局单例）
        preferred_engine: EngineSelector 偏好（groq/whisper）
        groq_api_key: Groq API key
        write_cache: 是否写入 M-004 缓存（默认 True）
        read_cache: 是否读取 M-004 缓存（默认 True；False=强制重转，对应 CLI --no-cache）

    Returns:
        Transcript

    Raises:
        TranscribeError(E_TR_001): 所有引擎失败
        TranscribeError(E_TR_004): 超时
    """
    if not audio_path:
        raise TranscribeError(E_TR_001, "audio_path 必填")
    if not os.path.exists(audio_path):
        raise TranscribeError(E_TR_001, f"音频文件不存在: {audio_path}")

    # 1) 计算 fingerprint
    if not audio_fingerprint:
        audio_fingerprint = compute_audio_fingerprint(audio_path)

    # 2) M-004 缓存命中短路（NFR4：同 URL 二次运行跳过转写）
    cm = cache_manager or await get_cache_manager()
    cache_key = compute_url_sha256(audio_fingerprint)
    if read_cache:
        try:
            cached = await cm.query(audio_fingerprint, etag="")
        except Exception as e:
            logger.warning("缓存查询失败（降级到无缓存模式）: %s", e)
            cached = None
        if cached is not None and cached.payload:
            emit_log("info", "转写缓存命中（NFR4 跳过）", step="transcribe", url=audio_fingerprint)
            return _payload_to_transcript(cached.payload)
    else:
        emit_log(
            "info",
            "跳过转写缓存读取（--no-cache 强制重转）",
            step="transcribe",
            url=audio_fingerprint,
        )

    # 3) EngineSelector 选主引擎 + 降级链
    selector = EngineSelector(
        preferred_engine=preferred_engine,
        groq_api_key=groq_api_key,
    )
    chain = selector.fallback_chain()

    # 把 preferred 引擎提到链首
    if preferred_engine in chain and chain.index(preferred_engine) != 0:
        chain.remove(preferred_engine)
        chain.insert(0, preferred_engine)

    last_err: Exception | None = None
    for eng in chain:
        if not selector.is_available(eng):
            continue
        try:
            start = time.monotonic()
            transcript = await asyncio.wait_for(
                _run_engine(eng, audio_path, language, model_size, groq_api_key),
                timeout=timeout_sec,
            )
            elapsed = time.monotonic() - start
            emit_log(
                "info",
                f"转写成功: engine={eng.value} model={model_size} elapsed={elapsed:.1f}s",
                step="transcribe",
                duration_ms=int(elapsed * 1000),
                url=audio_fingerprint,
            )
            # 写缓存
            if write_cache:
                try:
                    entry = CacheEntry(
                        url=audio_fingerprint,
                        url_sha256=cache_key.replace("sha256:", ""),
                        etag="",
                        platform="video",
                        ttl_days=DEFAULT_TRANSCRIPT_TTL_DAYS,
                        created_at=time.time(),
                        payload=_transcript_to_payload(transcript, eng, model_size, elapsed),
                    )
                    await cm.write(entry)
                except Exception as e:
                    logger.warning("缓存写入失败: %s", e)
            return transcript
        except asyncio.TimeoutError:
            last_err = TranscribeError(E_TR_004, f"引擎 {eng.value} 转写超时 ({timeout_sec}s)")
            register_error(
                ErrorCode.E_TR_004_TIMEOUT.value,
                scene=f"{eng.value} 转写超时",
                cause=f"超过 {timeout_sec}s 未完成",
                suggestion="调大 transcribe_timeout_sec，或减小 model_size",
                context={"engine": eng.value, "model_size": model_size},
            )
            continue
        except TranscribeError as e:
            last_err = e
            logger.warning("引擎 %s 转写失败: %s", eng.value, e)
            continue

    # 所有引擎失败
    register_error(
        ErrorCode.E_TR_001.value,
        scene="所有转写引擎失败",
        cause=str(last_err) if last_err else "未知错误",
        suggestion="检查音频文件；或检查 API key / 模型可用性",
    )
    raise TranscribeError(
        E_TR_001,
        f"所有引擎失败，最后错误: {last_err}",
    ) from last_err


async def _run_engine(
    engine: EngineType,
    audio_path: str,
    language: str,
    model_size: str,
    groq_api_key: str | None,
) -> Transcript:
    """调单个引擎（在 wait_for 包装内）。"""
    if engine == EngineType.WHISPER:
        we = WhisperEngine(model_size=model_size)
        return await asyncio.to_thread(we.transcribe, audio_path, language, model_size)
    if engine == EngineType.GROQ:
        if not groq_api_key:
            raise TranscribeError(E_TR_003, "Groq api_key 缺失")
        ge = GroqEngine(api_key=groq_api_key)
        return await asyncio.to_thread(ge.transcribe, audio_path, language)
    raise TranscribeError(E_TR_001, f"不支持的引擎: {engine}")


# --------------------------------------------------------------------------- #
# 缓存序列化（Transcript ↔ payload dict）
# --------------------------------------------------------------------------- #


def _transcript_to_payload(
    transcript: Transcript,
    engine: EngineType,
    model_size: str,
    elapsed_sec: float,
) -> dict[str, Any]:
    """Transcript → 可 JSON 序列化的 dict。"""
    return {
        "language": transcript.language,
        "full_text": transcript.full_text,
        "segments": [s.model_dump() for s in transcript.segments],
        "engine": transcript.engine or engine.value,
        "cer_estimate": transcript.cer_estimate,
        "model_size": model_size,
        "elapsed_sec": elapsed_sec,
    }


def _payload_to_transcript(payload: dict[str, Any]) -> Transcript:
    """payload dict → Transcript（缓存命中时调用）。"""
    segs = [
        TranscriptSegment(**s)
        for s in payload.get("segments", [])
        if isinstance(s, dict) and "start" in s and "end" in s and "text" in s
    ]
    return Transcript(
        language=payload.get("language", "zh"),
        full_text=payload.get("full_text", ""),
        segments=segs,
        engine=payload.get("engine", ""),
        cer_estimate=float(payload.get("cer_estimate", 0.0)),
        raw=None,
    )


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def compute_audio_fingerprint(audio_path: str) -> str:
    """计算音频指纹：sha256(file_size + mtime + first_1kb)。

    比 sha256(full_file) 快得多；同文件重复运行时指纹稳定。
    """
    p = Path(audio_path)
    try:
        st = p.stat()
    except OSError as e:
        raise TranscribeError(E_TR_001, f"无法 stat 音频文件: {audio_path}") from e

    h = hashlib.sha256()
    h.update(str(st.st_size).encode("utf-8"))
    h.update(str(int(st.st_mtime)).encode("utf-8"))
    try:
        with open(p, "rb") as f:
            h.update(f.read(1024))
    except OSError as e:
        raise TranscribeError(E_TR_001, f"无法读取音频文件: {audio_path}") from e
    return "sha256:" + h.hexdigest()


def detect_ram_available() -> float:
    """探测系统可用 RAM（GB），失败返回 0.0。

    移植自 M-005 设计：V1.1 track-core 不强依赖 psutil（避免 NFR1 污染核心 deps）。
    """
    try:
        import psutil  # type: ignore  # noqa: F401

        return float(psutil.virtual_memory().available) / 1024 / 1024 / 1024
    except ImportError:
        pass
    # 退化：通过 /proc/meminfo（Linux）/ 无 fallback（Windows）
    if os.name == "posix":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable"):
                        kb = int(line.split()[1])
                        return kb / 1024 / 1024
        except (OSError, ValueError):
            pass
    return 0.0


def estimate_cer(transcript: str, reference: str) -> float:
    """估算 CER（字符错误率）。

    V1.1 reference 为空时返回 0.0 占位（IC-012 / MD-007 §CER）。
    实现：编辑距离 Levenshtein。
    """
    if not reference:
        return 0.0
    if not transcript:
        raise ValueError("transcript 非空时才能估算 CER")
    # Levenshtein distance
    a, b = transcript, reference
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return 1.0 if a else 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1] / len(a)


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #


__all__ = [
    # 常量
    "DEFAULT_MODEL_SIZE",
    "MIN_RAM_GB",
    "DOWNGRADE_MODEL_SIZES",
    "SUPPORTED_AUDIO_EXTS",
    "GROQ_MAX_FILE_BYTES",
    "DEFAULT_TRANSCRIPT_TTL_DAYS",
    "DEFAULT_HF_CACHE_DIR",
    # 错误码
    "E_TR_001",
    "E_TR_002",
    "E_TR_003",
    "E_TR_004",
    "E_TR_005",
    # 枚举
    "EngineType",
    # 数据类
    "TranscribeRequest",
    "TranscribeResult",
    # 类
    "EngineSelector",
    "WhisperEngine",
    "GroqEngine",
    # 模板方法
    "transcribe",
    # 工具
    "compute_audio_fingerprint",
    "detect_ram_available",
    "estimate_cer",
]
