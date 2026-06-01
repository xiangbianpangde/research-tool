"""数据摄取适配器：把外部资料（PDF / 视频）转成 raw/ 文件，替代/补充 Collector。"""

from .cache_manager import (
    DEFAULT_CACHE_DIR,
    DEFAULT_TTL_DAYS,
    YOUTUBE_TTL_DAYS,
    CacheEntry,
    CacheManager,
    CacheRepository,
    cleanup_expired,
    compute_url_sha256,
    get_manager,
    query_cache,
    reset_singleton,
    write_cache,
)
from .ffmpeg_wrapper import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_KEYFRAME_COUNT,
    DEFAULT_KEYFRAME_DIRNAME,
    AudioExtractResult,
    AudioExtractor,
    FFmpegInvoker,
    KeyframeCapture,
    KeyframeResult,
    capture_keyframes,
    extract_audio,
    get_ffmpeg_invoker,
)
from .pdf import PdfIngestor, ingest_pdfs
from .preflight import (
    DEFAULT_TTL_SECONDS,
    DEFAULT_WHISPER_MODEL_SIZE,
    PreflightFacade,
    PreflightReport,
    check_all,
    invalidate_cache,
)

__all__ = [
    # PDF
    "PdfIngestor",
    "ingest_pdfs",
    # M-002 preflight
    "PreflightReport",
    "PreflightFacade",
    "check_all",
    "invalidate_cache",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_WHISPER_MODEL_SIZE",
    # M-004 cache
    "CacheEntry",
    "CacheRepository",
    "CacheManager",
    "compute_url_sha256",
    "get_manager",
    "query_cache",
    "write_cache",
    "cleanup_expired",
    "reset_singleton",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_TTL_DAYS",
    "YOUTUBE_TTL_DAYS",
    # M-009 ffmpeg
    "FFmpegInvoker",
    "AudioExtractor",
    "AudioExtractResult",
    "KeyframeCapture",
    "KeyframeResult",
    "get_ffmpeg_invoker",
    "extract_audio",
    "capture_keyframes",
    "DEFAULT_KEYFRAME_COUNT",
    "DEFAULT_AUDIO_FORMAT",
    "DEFAULT_KEYFRAME_DIRNAME",
]
